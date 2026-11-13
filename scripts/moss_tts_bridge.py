# -*- coding: UTF-8 -*-
"""
MOSS-TTS-Nano 独立推理桥接服务（零第三方 Web 依赖，标准库 http.server）。

用途：把模型跑在另一台机器 / 独立虚拟环境里，Flask 侧只需配置
      MOSS_TTS_BACKEND=http + MOSS_TTS_SERVICE_URL=http://<host>:18090

启动：
    python scripts/moss_tts_bridge.py --repo /path/to/MOSS-TTS-Nano --port 18090

协议：
    GET  /health                        -> {"status":"ok", ...}
    POST /tts                           -> {"text": "...",
                                            "reference_audio": "<wav base64>",
                                            "options": {"sample_mode","seed",
                                                        "cpu_threads","max_new_frames"}}
                                        <- {"audio_base64": "...",
                                            "sample_rate": 48000,
                                            "elapsed_seconds": 1.23}

可选鉴权：设置 MOSS_BRIDGE_TOKEN（或 --token）后 /tts 需带
Authorization: Bearer <token>；/health 始终公开，方便容器健康检查。
即使加了 token 也只建议在可信内网使用，不要直接暴露到公网。
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("moss-bridge")

_RUNTIME = None
_RUNTIME_LOCK = threading.Lock()
# 只允许少量请求排队，避免客户端断开后服务端还在无意义地排队推理
_QUEUE_LIMIT = 3
_QUEUE_SEM = threading.BoundedSemaphore(_QUEUE_LIMIT)
QUEUE_WAIT_SECONDS = 120


def build_runtime(repo: Path, model_dir: str | None, threads: int):
    sys.path.insert(0, str(repo))
    from onnx_tts_runtime import OnnxTtsRuntime  # type: ignore

    model = model_dir if model_dir and Path(model_dir).exists() else None
    logger.info("加载 ONNX 权重，model_dir=%s（缺省时首次自动下载）", model)
    return OnnxTtsRuntime(
        model_dir=model,
        thread_count=threads,
        max_new_frames=375,
        do_sample=True,
        sample_mode="fixed",
        execution_provider="cpu",
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "MossBridge/1.0"
    runtime = None
    max_bytes = 30 * 1024 * 1024
    max_audio_bytes = 20 * 1024 * 1024
    max_text_chars = 1000
    token = ""

    def _authorized(self) -> bool:
        if not self.token:
            return True
        supplied = self.headers.get("Authorization", "").strip()
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:].strip()
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") in ("/health", ""):
            self._send(200, {
                "status": "ok",
                "model": "MOSS-TTS-Nano-100M-ONNX (0.1B)",
                "runtime_loaded": self.runtime is not None,
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") != "/tts":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        if self.runtime is None:
            self._send(503, {"error": "ONNX runtime 未加载"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > self.max_bytes:
                self._send(413, {"error": "请求体为空或过大"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            text = str(body.get("text") or "").strip()
            audio = base64.b64decode(body.get("reference_audio") or "")
            if not text or not audio:
                self._send(400, {"error": "缺少 text 或 reference_audio"})
                return
            if len(text) > self.max_text_chars:
                self._send(400, {"error": "文本过长"})
                return
            if len(audio) > self.max_audio_bytes:
                self._send(413, {"error": "参考音频过大"})
                return
            options = body.get("options") or {}
        except (ValueError, KeyError, TypeError) as exc:
            self._send(400, {"error": f"请求解析失败：{exc}"})
            return

        if not _QUEUE_SEM.acquire(blocking=False):
            self._send(503, {"error": f"推理队列已满（最多 {_QUEUE_LIMIT} 个排队请求）"})
            return

        work = Path(tempfile.gettempdir()) / f"moss-bridge-{uuid.uuid4().hex[:12]}"
        work.mkdir(parents=True, exist_ok=True)
        ref, out = work / "ref.wav", work / "out.wav"
        try:
            ref.write_bytes(audio)
            sample_mode = str(options.get("sample_mode") or "fixed")
            if sample_mode not in ("fixed", "greedy", "full"):
                sample_mode = "fixed"
            seed = options.get("seed")
            started = time.perf_counter()
            if not _RUNTIME_LOCK.acquire(timeout=QUEUE_WAIT_SECONDS):
                self._send(503, {"error": "等待推理超时，请稍后重试"})
                return
            try:
                result = self.runtime.synthesize(
                    text=text,
                    voice="Junhao",
                    prompt_audio_path=str(ref),
                    output_audio_path=str(out),
                    sample_mode=sample_mode,
                    do_sample=sample_mode != "greedy",
                    streaming=True,
                    max_new_frames=int(options.get("max_new_frames") or 375),
                    voice_clone_max_text_tokens=75,
                    enable_wetext=False,
                    enable_normalize_tts_text=True,
                    seed=int(seed) if seed not in (None, "") else None,
                )
            finally:
                _RUNTIME_LOCK.release()
            if not out.is_file():
                raise RuntimeError("推理完成但未生成音频文件")
            self._send(200, {
                "audio_base64": base64.b64encode(out.read_bytes()).decode("ascii"),
                "sample_rate": int(result.get("sample_rate", 48000)),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "backend": "onnx",
                "demo": False,
            })
        except Exception as exc:  # 推理栈很杂，统一转成可读错误
            logger.exception("inference failed")
            self._send(500, {"error": str(exc)})
        finally:
            _QUEUE_SEM.release()
            shutil.rmtree(work, ignore_errors=True)

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    default_repo = Path(__file__).resolve().parents[1] / "third_party" / "MOSS-TTS-Nano"
    parser = argparse.ArgumentParser(description="MOSS-TTS-Nano HTTP bridge")
    parser.add_argument("--repo", default=str(default_repo), help="官方仓库路径")
    parser.add_argument("--model-dir", default="", help="ONNX 权重目录")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument(
        "--token", default=os.environ.get("MOSS_BRIDGE_TOKEN", ""),
        help="可选 Bearer token；也可用 MOSS_BRIDGE_TOKEN 环境变量",
    )
    parser.add_argument(
        "--queue-wait", type=int,
        default=int(os.environ.get("MOSS_BRIDGE_QUEUE_WAIT", "120")),
        help="排队等待推理锁的最长秒数",
    )
    args = parser.parse_args()

    global QUEUE_WAIT_SECONDS
    QUEUE_WAIT_SECONDS = max(1, args.queue_wait)
    Handler.token = args.token.strip()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "infer_onnx.py").is_file():
        parser.error(f"未在 {repo} 找到 infer_onnx.py，请先 clone 官方仓库或用 --repo 指定路径")

    Handler.runtime = build_runtime(repo, args.model_dir or str(repo / "models"), args.cpu_threads)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    def _shutdown(*_: object) -> None:
        logger.info("收到退出信号，正在停止")
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):
            pass

    logger.info(
        "桥接服务已启动: http://%s:%s（鉴权=%s，排队上限=%d）",
        args.host, args.port, "开启" if Handler.token else "关闭", _QUEUE_LIMIT,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("已停止")


if __name__ == "__main__":
    main()
