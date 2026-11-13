# -*- coding: UTF-8 -*-
"""语音克隆独立服务入口。

开发启动：python app.py   （默认 http://127.0.0.1:5002/v1）
生产启动：python wsgi.py  （waitress，单进程多线程 + 跨进程文件锁槽位）
接口前缀：/v1/voice-clone/*
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from flask import Flask, g, jsonify, request

from app.config.settings import get_settings, load_dotenv

load_dotenv()  # 必须早于 create_v1 / 引擎模块的 import

from app.api.v1 import create_v1  # noqa: E402
from app.config.protocol import StatusCode  # noqa: E402
from app.util.protocol_handler import ProtocolBuilder  # noqa: E402

logging.basicConfig(
    level=getattr(logging, get_settings().log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("voice-clone")

app = Flask(__name__, static_folder=None)  # 声纹/产物目录绝不作为静态资源暴露
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 参考音频上限 20MB + 余量
app.register_blueprint(create_v1(), url_prefix="/v1")


@app.before_request
def _begin_request() -> None:
    g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    g.started_at = time.perf_counter()


@app.after_request
def _finish_request(resp):
    cfg = get_settings()
    origin = request.headers.get("Origin", "").rstrip("/")
    if cfg.cors_allow_all:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in cfg.cors_origins:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers.add("Vary", "Origin")
    resp.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Requested-With, X-Request-Id"
    )
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["X-Request-Id"] = getattr(g, "request_id", "")
    elapsed = (time.perf_counter() - getattr(g, "started_at", time.perf_counter())) * 1000
    # 任务轮询每 0.8s 一次，INFO 级别会把日志刷满，降为 DEBUG
    is_poll = request.method == "GET" and request.path.startswith("/v1/voice-clone/jobs/")
    log = logger.debug if is_poll else logger.info
    log(
        "%s %s -> %s (%.1fms) rid=%s",
        request.method, request.path, resp.status_code, elapsed,
        getattr(g, "request_id", ""),
    )
    return resp


def _error_envelope(code: StatusCode, message: str):
    """框架级错误（413/404/405/500）也返回统一 envelope，前端 unwrap 才不会拿到 HTML。"""
    return jsonify(ProtocolBuilder.build_response(None, code, message)), code.value


@app.errorhandler(StatusCode.REQUEST_ENTITY_TOO_LARGE.value)
def _payload_too_large(_exc):
    return _error_envelope(
        StatusCode.REQUEST_ENTITY_TOO_LARGE, "上传内容超过 25MB 限制，请裁剪音频后重试"
    )


@app.errorhandler(StatusCode.NOT_FOUND.value)
def _route_not_found(_exc):
    return _error_envelope(StatusCode.NOT_FOUND, f"接口不存在：{request.path}")


@app.errorhandler(StatusCode.METHOD_NOT_ALLOWED.value)
def _method_not_allowed(_exc):
    return _error_envelope(StatusCode.METHOD_NOT_ALLOWED, "请求方法不被允许")


@app.errorhandler(StatusCode.INTERNAL_ERROR.value)
def _internal_error(_exc):
    # Flask 的 handle_exception 已经记录了完整栈，这里只负责把响应换成 envelope
    return _error_envelope(StatusCode.INTERNAL_ERROR, "服务内部错误，请查看服务端日志")


@app.get("/health")
def health():
    cfg = get_settings()
    return jsonify({
        "ok": True,
        "service": "voice-clone",
        "auth_enabled": cfg.auth_enabled,
        "backend": os.environ.get("MOSS_TTS_BACKEND", "auto"),
        "max_inference_slots": cfg.max_inference_slots,
    })


_MAINTENANCE_STARTED = False
_MAINTENANCE_LOCK = threading.Lock()


def start_maintenance() -> None:
    """后台定期回收：过期产物、过期任务、崩溃残留的锁文件。"""
    global _MAINTENANCE_STARTED
    with _MAINTENANCE_LOCK:
        if _MAINTENANCE_STARTED:
            return
        _MAINTENANCE_STARTED = True

    def _loop() -> None:
        while True:
            time.sleep(60)
            try:
                from app.util import inference_slots, jobs, moss_tts

                moss_tts.cleanup_outputs_now()
                jobs.cleanup_expired()
                inference_slots.cleanup_stale_files()
            except Exception:
                logger.exception("后台清理任务异常")

    threading.Thread(target=_loop, daemon=True, name="vc-maintenance").start()


start_maintenance()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5002"))
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
