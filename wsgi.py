# -*- coding: UTF-8 -*-
"""生产入口：优先用 waitress（单进程多线程），没装时退回 Flask 开发服务器。

注意：多进程部署会绕过进程内的任务表（jobs）与限流计数，
推理闸门用的是跨进程文件锁，所以多进程可以跑，但任务状态不共享；
推荐单进程 + 多线程，前面挂 Nginx。
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

from app.config.settings import load_dotenv

load_dotenv()

# 根目录的 app.py 与 app/ 包同名，直接 `from app import app` 会命中包而不是模块，
# 所以按文件路径加载（与 scripts/smoke_test.py 的做法一致）。
ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("voice_clone_app", ROOT / "app.py")
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
app = _mod.app
start_maintenance = _mod.start_maintenance

logger = logging.getLogger("voice-clone")


def main() -> None:
    start_maintenance()
    # 默认只监听本机；容器 / 对外部署时显式设置 HOST=0.0.0.0，并在前面挂反向代理
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5002"))
    threads = int(os.environ.get("WEB_CONCURRENCY", "8"))
    try:
        from waitress import serve
    except ImportError:
        logger.warning("未安装 waitress，回退到 Flask 开发服务器（不建议用于生产）")
        app.run(host=host, port=port, threaded=True, debug=False)
        return
    logger.info("waitress 启动：http://%s:%s threads=%d", host, port, threads)
    serve(app, host=host, port=port, threads=threads, channel_timeout=120)


if __name__ == "__main__":
    main()
