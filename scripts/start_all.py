# -*- coding: UTF-8 -*-
"""一键启动：按 .env 中的 MOSS_TTS_BACKEND 决定是否拉起推理 bridge。

  python scripts/start_all.py                # 按 .env 自动判断
  python scripts/start_all.py --no-bridge    # 强制不启 bridge
  python scripts/start_all.py --no-wait      # 不等 bridge 健康检查

比 start.bat/sh 更可靠：只在使用 http 后端时才启动 bridge（避免白占 728MB 内存），
等待 /health 就绪后再启动主服务，Ctrl+C 时把子进程一起收掉。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import PROJECT_ROOT, load_dotenv  # noqa: E402

load_dotenv()


def python_bin() -> str:
    venv = PROJECT_ROOT / ".venv-moss"
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = venv / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def wait_health(url: str, timeout: int = 300) -> bool:
    endpoint = url.rstrip("/") + "/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as resp:
                if resp.status == 200:
                    print(f"[start] 推理服务已就绪：{endpoint}", flush=True)
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(1)
    print(f"[start] 等待推理服务超时（{timeout}s），仍继续启动主服务", file=sys.stderr)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="语音克隆服务一键启动")
    parser.add_argument("--no-bridge", action="store_true", help="不启动推理 bridge")
    parser.add_argument("--no-wait", action="store_true", help="不等待 bridge 健康检查")
    args = parser.parse_args()

    backend = os.environ.get("MOSS_TTS_BACKEND", "auto").strip().lower()
    service_url = os.environ.get("MOSS_TTS_SERVICE_URL", "http://127.0.0.1:18090").strip()
    py = python_bin()
    children: list[subprocess.Popen] = []

    if backend == "http" and not args.no_bridge:
        port = 18090
        try:
            port = int(service_url.rsplit(":", 1)[-1].split("/")[0])
        except ValueError:
            pass
        bridge = subprocess.Popen(
            [py, "scripts/moss_tts_bridge.py", "--port", str(port)],
            cwd=str(PROJECT_ROOT),
        )
        children.append(bridge)
        print(f"[start] 已启动推理 bridge（pid={bridge.pid} port={port}）", flush=True)
        if not args.no_wait:
            wait_health(service_url)
    else:
        print(f"[start] backend={backend}，跳过 bridge", flush=True)

    api = subprocess.Popen([py, "wsgi.py"], cwd=str(PROJECT_ROOT))
    children.append(api)
    print(f"[start] 已启动主服务（pid={api.pid}）", flush=True)

    try:
        api.wait()
    except KeyboardInterrupt:
        print("\n[start] 正在停止全部服务…", flush=True)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
