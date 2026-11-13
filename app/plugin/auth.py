# -*- coding: UTF-8 -*-
"""鉴权：单机免登录 / 多用户 API Key 两种模式。

安全约定：
  * uid 只由服务端决定，**绝不接受前端的 X-User-Id 之类的请求头**；
  * 未配置任何 key 时为单机模式，所有档案归到 local；
  * 配置 VOICE_CLONE_API_KEY 后必须带 Bearer，比较使用 hmac.compare_digest；
  * 多人隔离用 VOICE_CLONE_USERS='{"<key>":"<uid>", ...}'，key 决定 uid。
"""
from __future__ import annotations

import functools
import hmac
from typing import Any, Callable

from flask import g, jsonify, request

from app.config.protocol import StatusCode
from app.config.settings import get_settings
from app.util.protocol_handler import ProtocolBuilder

LOCAL_UID = "local"


def _bearer_token() -> str:
    supplied = request.headers.get("Authorization", "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    return supplied


def resolve_uid() -> str | None:
    """返回当前请求的 uid；未通过校验返回 None。"""
    cfg = get_settings()
    if not cfg.auth_enabled:
        return LOCAL_UID
    supplied = _bearer_token()
    if not supplied:
        return None
    for key, uid in cfg.api_users.items():
        if hmac.compare_digest(supplied, key):
            return uid
    return None


def current_uid() -> str:
    payload = getattr(g, "jwt_payload", None) or {}
    return str(payload.get("sub") or LOCAL_UID).strip()[:64] or LOCAL_UID


def token_required(f: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        uid = resolve_uid()
        if uid is None:
            return (
                jsonify(
                    ProtocolBuilder.build_response(
                        None, StatusCode.UNAUTHORIZED, "未授权：请携带正确的 API Key"
                    )
                ),
                StatusCode.UNAUTHORIZED.value,
            )
        g.jwt_payload = {"sub": uid}
        return f(*args, **kwargs)

    return wrapper
