# -*- coding: UTF-8 -*-
"""纯内存滑动窗口限流（独立版：不依赖 Redis，单进程部署够用）。

每个「用户 + 端点」维护一个时间戳队列；过期条目按需弹出，空队列定期回收，
避免 _COUNTERS 无限增长。多进程部署请在网关层再做一次全局限流。
"""
from __future__ import annotations

import functools
import math
import threading
import time
from collections import deque

from flask import g, jsonify, request

from app.config.protocol import StatusCode
from app.util.protocol_handler import ProtocolBuilder

_COUNTERS: dict[str, deque[float]] = {}
_LOCK = threading.Lock()
_MAX_KEYS = 5000


def _identity() -> str:
    """优先用服务端解析出的 uid；匿名请求（如 /health 之外）才退回 IP。"""
    payload = getattr(g, "jwt_payload", None) or {}
    uid = str(payload.get("sub") or "").strip()
    if uid:
        return f"u:{uid}"
    return f"ip:{request.remote_addr or 'anonymous'}"


def reset_counters() -> None:
    """测试用：清空全部计数。"""
    with _LOCK:
        _COUNTERS.clear()


def rate_limit(max_requests: int = 30, window_seconds: int = 60):
    """按「用户 + 端点」计数，超限返回标准 429 envelope + Retry-After。"""

    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            key = f"{_identity()}:{request.endpoint or f.__name__}"
            now = time.monotonic()
            retry = 1
            with _LOCK:
                bucket = _COUNTERS.setdefault(key, deque())
                while bucket and now - bucket[0] >= window_seconds:
                    bucket.popleft()
                if len(bucket) >= max_requests:
                    allowed = False
                    retry = max(1, math.ceil(window_seconds - (now - bucket[0])))
                else:
                    allowed = True
                    bucket.append(now)
                if len(_COUNTERS) > _MAX_KEYS:
                    for stale_key in [k for k, v in _COUNTERS.items() if not v]:
                        _COUNTERS.pop(stale_key, None)
            if not allowed:
                resp = jsonify(
                    ProtocolBuilder.build_response(
                        None,
                        StatusCode.TOO_MANY_REQUESTS,
                        f"请求过于频繁，请 {retry} 秒后再试",
                    )
                )
                resp.headers["Retry-After"] = str(retry)
                return resp, StatusCode.TOO_MANY_REQUESTS.value
            return f(*args, **kwargs)

        return wrapper

    return decorator
