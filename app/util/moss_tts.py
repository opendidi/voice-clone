# -*- coding: UTF-8 -*-
"""
MOSS-TTS-Nano (0.1B) 语音克隆引擎适配层 —— 仅用于本地功能测试。

官方仓库: https://github.com/OpenMOSS/MOSS-TTS-Nano
官方 ONNX CPU 推理入口 infer_onnx.py 的关键参数（已核对源码）::

    python infer_onnx.py \
        --prompt-audio-path ref.wav \
        --text-file text.txt \
        --output-audio-path out.wav \
        --output-audio-path ... --model-dir ... --cpu-threads 4

in-process 模式直接调用 onnx_tts_runtime.OnnxTtsRuntime.synthesize(
    text, voice, prompt_audio_path, output_audio_path, sample_mode,
    do_sample, streaming, max_new_frames, voice_clone_max_text_tokens,
    enable_wetext, enable_normalize_tts_text, seed
)

四种后端（MOSS_TTS_BACKEND）:
  auto  —— 依次探测 in-process ONNX / CLI ONNX / 远程 HTTP，取第一个可用
  onnx  —— 本机 ONNX（优先 in-process 常驻，失败降级 CLI 子进程）
  cli   —— 只走 infer_onnx.py 子进程，可用独立 conda 环境（MOSS_TTS_PYTHON）
  http  —— 调用远程推理服务（配合 scripts/moss_tts_bridge.py）
  mock  —— 生成提示音，仅用于前后端链路联调，绝不伪装成真实克隆

环境变量（全部可选，均有默认值）:
  MOSS_TTS_REPO        官方仓库本地路径，默认 <项目根>/third_party/MOSS-TTS-Nano
  MOSS_TTS_PYTHON      运行 infer_onnx.py 的解释器，默认当前 sys.executable
  MOSS_TTS_MODEL_DIR   ONNX 权重目录，默认 <repo>/models
  MOSS_TTS_SERVICE_URL 远程推理服务，如 http://127.0.0.1:18090
  MOSS_TTS_TIMEOUT     单次推理超时秒数，默认 600
  MOSS_TTS_CPU_THREADS onnxruntime 线程数，默认 4

设计取舍:
  * in-process / CLI 都显式关闭 WeTextProcessing（--disable-wetext-processing /
    enable_wetext=False），规避 Windows 上 pynini 编译安装困难的问题；
    normalize_tts_text 仍保留，中文标点/数字清洗足够用。
  * 不把模型依赖写进主 requirements.txt，见 requirements-moss-tts.txt。
  * 引擎未安装时不抛栈、不静默造假，通过 probe() 返回可执行的安装指引。
  * 产物落在 var/ 数据目录（不是静态资源目录），元数据带 owner 供归属校验；
    每次 synthesize 统一占用 inference_slots 的跨进程槽位，避免多路推理打爆内存。
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from app.config.settings import PROJECT_ROOT as _PROJECT_ROOT
from app.config.settings import get_settings
from app.util import inference_slots

logger = logging.getLogger(__name__)

PROJECT_ROOT = _PROJECT_ROOT


def output_dir() -> Path:
    """产物目录（默认 <root>/var/voice-clone），不是静态资源目录。"""
    return get_settings().output_dir


def tmp_dir() -> Path:
    return output_dir() / "_tmp"


MODEL_NAME = "MOSS-TTS-Nano-100M-ONNX (0.1B, 48kHz stereo, CPU)"
MAX_REF_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 1000
MIN_REF_SECONDS = 0.8
MAX_REF_SECONDS = 30.0
VALID_SAMPLE_MODES = ("fixed", "greedy", "full")

_VALID_BACKENDS = ("auto", "onnx", "cli", "http", "mock")
_CLEANUP_LOCK = threading.Lock()


class EngineNotReady(RuntimeError):
    """推理引擎尚未安装/启动，附带面向用户的解决指引。"""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class InferenceError(RuntimeError):
    """推理过程失败（模型报错、超时、服务异常等）。"""


@dataclass
class MossConfig:
    backend: str = "auto"
    repo_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "third_party" / "MOSS-TTS-Nano")
    python_bin: str = ""
    model_dir: str = ""
    service_url: str = ""
    timeout: int = 600
    cpu_threads: int = 4


def _default_python() -> str:
    """优先用 setup_moss_tts.py 建的独立环境，免去手工配 MOSS_TTS_PYTHON。"""
    explicit = os.environ.get("MOSS_TTS_PYTHON", "").strip()
    if explicit:
        return explicit
    venv = PROJECT_ROOT / ".venv-moss"
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = venv / rel
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def get_config() -> MossConfig:
    """每次调用重新读取环境变量，方便本地调试时改配置不必重启整个应用。"""
    backend = os.environ.get("MOSS_TTS_BACKEND", "auto").strip().lower() or "auto"
    if backend not in _VALID_BACKENDS:
        logger.warning("未知的 MOSS_TTS_BACKEND=%s，回退到 auto", backend)
        backend = "auto"

    def _int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, default)))
        except (TypeError, ValueError):
            return default

    repo = Path(
        os.environ.get("MOSS_TTS_REPO", "")
        or (PROJECT_ROOT / "third_party" / "MOSS-TTS-Nano")
    ).expanduser().resolve()
    model_dir = os.environ.get("MOSS_TTS_MODEL_DIR", "").strip() or str(repo / "models")

    return MossConfig(
        backend=backend,
        repo_dir=repo,
        python_bin=_default_python(),
        model_dir=model_dir,
        service_url=os.environ.get("MOSS_TTS_SERVICE_URL", "").strip().rstrip("/"),
        timeout=_int("MOSS_TTS_TIMEOUT", 600),
        cpu_threads=_int("MOSS_TTS_CPU_THREADS", 4),
    )


@dataclass
class SynthOptions:
    sample_mode: str = "fixed"
    seed: int | None = None
    cpu_threads: int | None = None
    max_new_frames: int = 375
    backend_override: str = ""

    def __post_init__(self) -> None:
        if self.sample_mode not in VALID_SAMPLE_MODES:
            self.sample_mode = "fixed"
        self.max_new_frames = min(1500, max(32, int(self.max_new_frames)))
        if self.cpu_threads is not None:
            self.cpu_threads = min(32, max(1, int(self.cpu_threads)))


def _ensure_dirs() -> None:
    output_dir().mkdir(parents=True, exist_ok=True)
    tmp_dir().mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# 参考音频校验
# --------------------------------------------------------------------------- #
def inspect_wav(path: Path) -> dict[str, Any]:
    """用标准库读取 WAV 元信息，避免为校验功能引入 soundfile/librosa 依赖。"""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        width = wf.getsampwidth()
        frames = wf.getnframes()
        duration = frames / float(rate) if rate else 0.0
    return {
        "channels": channels,
        "sample_rate": rate,
        "sample_width": width,
        "frames": frames,
        "duration": round(duration, 3),
        "size": path.stat().st_size,
    }


def validate_reference_audio(path: Path) -> dict[str, Any]:
    """前端已统一转成 WAV；后端严格要求 RIFF/WAVE，防止把任意文件喂给模型。"""
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError("参考音频为空")
    if path.stat().st_size > MAX_REF_BYTES:
        raise ValueError(f"参考音频过大（上限 {MAX_REF_BYTES // 1024 // 1024}MB）")
    with path.open("rb") as f:
        head = f.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("参考音频必须是 WAV 格式（前端会自动转换，请检查上传链路）")
    try:
        info = inspect_wav(path)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"无法解析 WAV 文件：{exc}") from exc

    if info["sample_width"] != 2:
        raise ValueError("参考音频需为 16-bit PCM WAV")
    if info["channels"] not in (1, 2):
        raise ValueError("参考音频声道数需为单声道或双声道")
    if not (8000 <= info["sample_rate"] <= 192000):
        raise ValueError(f"参考音频采样率异常：{info['sample_rate']}")
    if info["duration"] < MIN_REF_SECONDS:
        raise ValueError(f"参考音频太短（{info['duration']}s），建议 3~10 秒清晰人声")
    if info["duration"] > MAX_REF_SECONDS:
        raise ValueError(f"参考音频太长（{info['duration']}s），请裁剪到 30 秒以内")
    return info


# --------------------------------------------------------------------------- #
# 环境探测
# --------------------------------------------------------------------------- #
def _find_module(repo: Path, module: str) -> bool:
    """把官方仓库临时加入 sys.path 后探测模块是否存在。"""
    p = str(repo)
    sys.path.insert(0, p)
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False
    finally:
        try:
            sys.path.remove(p)
        except ValueError:
            pass


def _deps_available() -> bool:
    """当前解释器能否真正 import 官方 runtime（模块文件在仓库里不代表依赖齐了）。"""
    for name in ("onnxruntime", "numpy", "soundfile"):
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError, ModuleNotFoundError):
            return False
    return True


def _service_alive(url: str, timeout: float = 1.0) -> bool:
    if not url:
        return False
    for suffix in ("/health", "/openapi.json"):
        try:
            resp = requests.get(url + suffix, timeout=timeout)
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def _probe_uncached() -> dict[str, Any]:
    """返回引擎就绪状态 + 可直接复制的安装指引，供前端状态卡片渲染。"""
    cfg = get_config()
    repo = cfg.repo_dir
    infer_script = repo / "infer_onnx.py"
    weights = Path(cfg.model_dir)
    weights_ready = weights.exists() and any(weights.rglob("*.onnx"))

    cli_ready = infer_script.is_file()
    # 独立 venv 场景下主进程没有 onnxruntime，必须走 CLI，不能误选 in-process
    inprocess_ready = cli_ready and _deps_available() and _find_module(repo, "onnx_tts_runtime")
    service_ready = _service_alive(cfg.service_url)

    checks = {
        "repo": {
            "ok": repo.is_dir(),
            "detail": str(repo),
            "label": "官方仓库",
        },
        "infer_script": {
            "ok": cli_ready,
            "detail": str(infer_script) if cli_ready else "缺少 infer_onnx.py",
            "label": "ONNX 推理脚本",
        },
        "runtime_module": {
            "ok": inprocess_ready,
            "detail": (
                "onnx_tts_runtime 可导入（主进程常驻）"
                if inprocess_ready
                else "走独立环境 CLI 推理，主进程无需加载模型依赖"
                if cli_ready
                else "需安装 onnxruntime / numpy / soundfile"
            ),
            "label": "本地推理依赖",
        },
        "weights": {
            "ok": weights_ready,
            "detail": str(weights) if weights_ready else "未下载，首次推理会自动拉取",
            "label": "ONNX 权重",
        },
        "service": {
            "ok": service_ready,
            "detail": cfg.service_url or "未配置 MOSS_TTS_SERVICE_URL",
            "label": "远程推理服务",
        },
    }

    available = []
    if inprocess_ready or cli_ready:
        available.append("onnx")
    if service_ready:
        available.append("http")
    available.append("mock")

    backend = _resolve_backend(cfg, inprocess_ready, cli_ready, service_ready)
    ready = backend not in ("", "unavailable")
    demo = backend == "mock"

    repo_hint = str(repo)
    # 用绝对路径引用项目根的 requirements，避免 cd 到仓库后相对路径找不到文件；
    # 解释器也显式指向独立 venv，防止把 ONNX 依赖装进别的环境。
    py = _default_python()
    req_file = PROJECT_ROOT / "requirements-moss-tts.txt"
    setup = [
        "git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git " + repo_hint,
        f'"{py}" -m pip install -r "{req_file}"',
        "cd " + repo_hint,
        f'"{py}" infer_onnx.py --prompt-audio-path assets/audio/zh_1.wav '
        '--text "测试一下语音克隆"',
        "# 首次运行会自动下载 ONNX 权重到 models/ 目录",
    ]
    if backend in ("onnx", "cli"):
        hint = "环境就绪，可以开始真实语音克隆。"
        if backend == "cli":
            hint += "推理在独立虚拟环境中执行（CLI 子进程），主进程无需加载模型依赖。"
    elif backend == "http":
        hint = "远程推理服务已连接，可以开始真实语音克隆。"
    elif backend == "mock":
        hint = "当前为演示模式，生成结果不会经过模型推理。"
    elif not repo.is_dir():
        hint = "尚未放置 MOSS-TTS-Nano 官方仓库，按下方命令准备环境后刷新状态。"
    else:
        hint = "仓库已就位但缺少推理依赖，可运行 python scripts/setup_moss_tts.py 准备环境。"

    return {
        "ready": ready,
        "backend": backend or "unavailable",
        "mode": "demo" if demo else ("real" if ready else "unavailable"),
        "model": MODEL_NAME,
        "allow_backend_override": get_settings().allow_backend_override,
        "max_inference_slots": get_settings().max_inference_slots,
        "long_tts_max_chars": get_settings().long_tts_max_chars,
        "checks": checks,
        "available_backends": available,
        "config": {
            "backend": cfg.backend,
            "repo_dir": str(repo),
            "python_bin": cfg.python_bin,
            "model_dir": cfg.model_dir,
            "service_url": cfg.service_url or "(未配置)",
            "timeout": cfg.timeout,
            "cpu_threads": cfg.cpu_threads,
        },
        "limits": {
            "max_ref_bytes": MAX_REF_BYTES,
            "max_text_chars": MAX_TEXT_CHARS,
            "min_ref_seconds": MIN_REF_SECONDS,
            "max_ref_seconds": MAX_REF_SECONDS,
        },
        "hint": hint,
        "setup_commands": setup,
    }


_PROBE_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}
_PROBE_LOCK = threading.Lock()


def probe(force: bool = False) -> dict[str, Any]:
    """带 TTL 的探测结果。

    一次探测会 rglob 728MB 权重目录并发 HTTP 探活，合成请求每次都做纯属浪费；
    /status 的刷新按钮传 force=True 可强制重探。
    """
    ttl = get_settings().probe_cache_seconds
    if ttl <= 0:
        return _probe_uncached()
    with _PROBE_LOCK:
        now = time.monotonic()
        cached = _PROBE_CACHE["value"]
        if not force and cached is not None and now - _PROBE_CACHE["ts"] < ttl:
            return dict(cached)
        value = _probe_uncached()
        _PROBE_CACHE["value"] = value
        _PROBE_CACHE["ts"] = now
        return dict(value)


def invalidate_probe_cache() -> None:
    with _PROBE_LOCK:
        _PROBE_CACHE["value"] = None
        _PROBE_CACHE["ts"] = 0.0


def _resolve_backend(
    cfg: MossConfig, inprocess_ready: bool, cli_ready: bool, service_ready: bool
) -> str:
    requested = cfg.backend
    if requested == "mock":
        return "mock"
    if requested == "http":
        # 显式指定 http 也要确认服务真的活着，否则 bridge 挂了 status 会误报就绪
        return "http" if (cfg.service_url and service_ready) else ""
    if requested == "cli":
        return "cli" if cli_ready else ""
    if requested == "onnx":
        if inprocess_ready:
            return "onnx"
        return "cli" if cli_ready else ""
    # auto
    if inprocess_ready:
        return "onnx"
    if cli_ready:
        return "cli"
    if service_ready and cfg.service_url:
        return "http"
    return ""


# --------------------------------------------------------------------------- #
# 各后端实现
# --------------------------------------------------------------------------- #
_RUNTIME: Any = None
_RUNTIME_KEY: tuple[Any, ...] = ()
_RUNTIME_LOCK = threading.Lock()


def _get_runtime(cfg: MossConfig, opts: SynthOptions) -> Any:
    """ONNX session 非线程安全：全局单例 + 串行锁，避免每次请求重新加载权重。

    缓存 key 只含影响 session 构建的参数（仓库 / 权重 / 线程数）；
    max_new_frames / sample_mode 等采样参数由 _synthesize_onnx 在每次
    synthesize 调用时按需传入（官方 runtime 原生支持 per-call 覆盖），
    否则 key 里一旦带上帧数，不同长度的文本就会反复重载约 730MB 权重。
    """
    global _RUNTIME, _RUNTIME_KEY
    threads = opts.cpu_threads or cfg.cpu_threads
    key = (str(cfg.repo_dir), cfg.model_dir, threads)
    if _RUNTIME is not None and _RUNTIME_KEY == key:
        return _RUNTIME

    p = str(cfg.repo_dir)
    if p not in sys.path:
        sys.path.insert(0, p)
    from onnx_tts_runtime import OnnxTtsRuntime  # type: ignore  # noqa: E402

    # 默认不传 model_dir：交给官方 runtime 自动检查 <repo>/models 并按需下载。
    # 只有用户显式配了 MOSS_TTS_MODEL_DIR 才覆盖，避免传错层级导致找不到 onnx 图。
    model_dir = os.environ.get("MOSS_TTS_MODEL_DIR", "").strip() or None
    _RUNTIME = OnnxTtsRuntime(
        model_dir=model_dir,
        thread_count=threads,
        execution_provider="cpu",
    )
    _RUNTIME_KEY = key
    return _RUNTIME


def _synthesize_onnx(
    cfg: MossConfig, opts: SynthOptions, text: str, ref: Path, out: Path, work: Path,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        raise InferenceError("任务已取消")
    with _RUNTIME_LOCK:
        runtime = _get_runtime(cfg, opts)
        # 采样参数每次调用都显式传入：帧数 / 采样模式可随请求变化，不重建 session
        result = runtime.synthesize(
            text=text,
            voice="Junhao",
            prompt_audio_path=str(ref),
            output_audio_path=str(out),
            sample_mode=opts.sample_mode,
            do_sample=opts.sample_mode != "greedy",
            streaming=True,
            max_new_frames=opts.max_new_frames,
            voice_clone_max_text_tokens=75,
            # 关闭 WeTextProcessing：Windows 上 pynini 极难安装，且中文场景收益有限
            enable_wetext=False,
            enable_normalize_tts_text=True,
            seed=opts.seed,
        )
    return {
        "sample_rate": int(result.get("sample_rate", 48000)),
        "log": f"in-process ONNX 推理完成（{runtime.execution_provider}）",
    }


def _terminate_process(proc: subprocess.Popen) -> None:
    """尽力终止推理子进程，避免取消后还占着 CPU / 内存。"""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("推理进程未能终止：pid=%s", proc.pid)


def _synthesize_cli(
    cfg: MossConfig, opts: SynthOptions, text: str, ref: Path, out: Path, work: Path,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """子进程方式：允许模型跑在独立 conda / venv 里，不污染 Flask 主环境。

    用 Popen + 轮询而不是 subprocess.run，任务取消时才能真正杀掉推理进程；
    日志重定向到文件，避免管道缓冲区被模型日志打满导致死锁。
    """
    script = cfg.repo_dir / "infer_onnx.py"
    if not script.is_file():
        raise EngineNotReady("找不到 infer_onnx.py", "请检查 MOSS_TTS_REPO 配置")

    # 长文本走 --text-file，规避 Windows 命令行 8191 字符限制和参数转义问题
    text_file = work / "prompt.txt"
    text_file.write_text(text, encoding="utf-8")
    threads = opts.cpu_threads or cfg.cpu_threads

    cmd = [
        cfg.python_bin,
        str(script),
        "--prompt-audio-path", str(ref),
        "--text-file", str(text_file),
        "--output-audio-path", str(out),
        "--sample-mode", opts.sample_mode,
        "--cpu-threads", str(threads),
        "--max-new-frames", str(opts.max_new_frames),
        "--disable-wetext-processing",
    ]
    explicit_model_dir = os.environ.get("MOSS_TTS_MODEL_DIR", "").strip()
    if explicit_model_dir:
        cmd += ["--model-dir", explicit_model_dir]
    if opts.seed is not None:
        cmd += ["--seed", str(opts.seed)]

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Windows 上 Python 的 open() 默认走 GBK，会把中文 prompt.txt 读成乱码
    env["PYTHONUTF8"] = "1"
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    log_path = work / "inference.log"
    deadline = time.monotonic() + cfg.timeout
    with log_path.open("w", encoding="utf-8") as log_file:
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cfg.repo_dir), stdout=log_file,
                stderr=subprocess.STDOUT, env=env,
            )
        except (OSError, ValueError) as exc:
            raise InferenceError(f"无法启动推理进程：{exc}") from exc
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(proc)
                raise InferenceError("任务已取消，推理进程已终止")
            if time.monotonic() > deadline:
                _terminate_process(proc)
                raise InferenceError(
                    f"推理超时（{cfg.timeout}s），可减小文本长度或调大 MOSS_TTS_TIMEOUT"
                )
            time.sleep(0.2)

    with log_path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - 4096))
        tail = handle.read().decode("utf-8", errors="replace").strip()[-1200:]

    if proc.returncode != 0 or not out.is_file():
        raise InferenceError(f"推理进程退出码 {proc.returncode}：{tail or '未生成音频文件'}")
    return {"sample_rate": 48000, "log": tail[-400:]}


def _synthesize_http(
    cfg: MossConfig, opts: SynthOptions, text: str, ref: Path, out: Path, work: Path,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """远程推理服务协议见 scripts/moss_tts_bridge.py。"""
    if cancel_event is not None and cancel_event.is_set():
        raise InferenceError("任务已取消")
    if not cfg.service_url:
        raise EngineNotReady("未配置远程推理服务", "设置 MOSS_TTS_SERVICE_URL 或改用本机 ONNX 后端")
    payload = {
        "text": text,
        "reference_audio": base64.b64encode(ref.read_bytes()).decode("ascii"),
        "reference_filename": ref.name,
        "options": {
            "sample_mode": opts.sample_mode,
            "seed": opts.seed,
            "cpu_threads": opts.cpu_threads or cfg.cpu_threads,
            "max_new_frames": opts.max_new_frames,
        },
    }
    headers = {}
    bridge_token = get_settings().bridge_token
    if bridge_token:
        headers["Authorization"] = f"Bearer {bridge_token}"
    # bridge 的请求体上限 30MB，20MB WAV 转 base64 约 26.7MB，提前拦掉边界情况
    if len(payload["reference_audio"]) > 28 * 1024 * 1024:
        raise InferenceError("参考音频过大，远程推理服务无法接收，请裁剪后重试")
    try:
        resp = requests.post(
            cfg.service_url + "/tts", json=payload, timeout=cfg.timeout, headers=headers
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise InferenceError(f"远程推理服务调用失败：{exc}") from exc
    except ValueError as exc:
        raise InferenceError("远程推理服务返回的不是合法 JSON") from exc

    audio_b64 = body.get("audio_base64") or body.get("audio")
    if not audio_b64:
        raise InferenceError(f"远程服务未返回音频：{body.get('error') or body}")
    try:
        out.write_bytes(base64.b64decode(audio_b64))
    except (ValueError, TypeError) as exc:
        raise InferenceError("远程服务返回的音频 base64 无法解码") from exc
    return {
        "sample_rate": int(body.get("sample_rate", 48000)),
        "log": f"远程服务 {cfg.service_url} 推理完成",
    }


def _write_demo_wav(path: Path, seconds: float = 1.4, rate: int = 48000) -> None:
    """演示模式产物：一段柔和提示音，明确区别于任何真实人声。"""
    total = int(seconds * rate)
    buf = bytearray()
    for i in range(total):
        t = i / rate
        env = min(1.0, t / 0.05, (seconds - t) / 0.2)
        env = max(0.0, env)
        value = 0.20 * math.sin(2 * math.pi * 523.25 * t) + 0.10 * math.sin(2 * math.pi * 784.0 * t)
        buf += struct.pack("<h", int(max(-1.0, min(1.0, value * env)) * 32767))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(buf))


def _synthesize_mock(
    cfg: MossConfig, opts: SynthOptions, text: str, ref: Path, out: Path, work: Path,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    # wait 会在取消时提前返回 True，演示模式也能即时响应取消
    if cancel_event is not None and cancel_event.wait(0.6):
        raise InferenceError("任务已取消")
    _write_demo_wav(out)
    return {
        "sample_rate": 48000,
        "log": "演示模式：仅生成提示音，未加载模型、未做任何声纹克隆",
    }


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def synthesize(
    text: str,
    ref: Path,
    opts: SynthOptions | None = None,
    *,
    owner: str = "local",
    cancel_event: threading.Event | None = None,
    slot_already_held: bool = False,
    progress: Any = None,
) -> dict[str, Any]:
    """执行一次语音克隆，返回可直接写进 API 响应的结果字典。

    owner 写进产物元数据，/audio、/meta 据此做归属校验；
    所有后端统一受 inference_slot 跨进程并发闸门约束。
    """
    opts = opts or SynthOptions()
    cfg = get_config()
    _ensure_dirs()

    status = probe()
    backend = status["backend"]
    override = (opts.backend_override or "").strip().lower()
    if override == "mock":
        # 演示模式不加载模型、不消耗 CPU，允许前端随时切过去验证链路
        backend = "mock"
    elif override and get_settings().allow_backend_override:
        if override not in _VALID_BACKENDS:
            raise ValueError(f"不支持的后端：{override}")
        if override == "http":
            if not cfg.service_url:
                raise EngineNotReady("远程服务未配置", "请设置 MOSS_TTS_SERVICE_URL")
            backend = "http"
        elif override == "cli":
            if not status["checks"]["infer_script"]["ok"]:
                raise EngineNotReady("CLI 后端不可用", status["hint"])
            backend = "cli"
        elif override == "onnx":
            if status["checks"]["runtime_module"]["ok"]:
                backend = "onnx"
            elif status["checks"]["infer_script"]["ok"]:
                backend = "cli"
            else:
                raise EngineNotReady("本机 ONNX 后端不可用", status["hint"])
    elif override:
        # 后端由服务端配置决定，默认不接受前端指定（避免绕过探测、打爆机器）
        logger.debug("已忽略前端 backend override=%s", override)
    if backend in ("", "unavailable"):
        raise EngineNotReady("MOSS-TTS-Nano 推理引擎未就绪", status["hint"])

    job = uuid.uuid4().hex[:16]
    work = tmp_dir() / job
    work.mkdir(parents=True, exist_ok=True)
    out = work / "output.wav"

    handlers = {
        "onnx": _synthesize_onnx,
        "cli": _synthesize_cli,
        "http": _synthesize_http,
        "mock": _synthesize_mock,
    }
    started = time.perf_counter()
    slot_ctx = (
        contextlib.nullcontext()
        if slot_already_held
        else inference_slots.inference_slot(cancel_event=cancel_event)
    )
    try:
        with slot_ctx:
            if cancel_event is not None and cancel_event.is_set():
                raise InferenceError("任务已取消")
            if progress is not None:
                progress("正在推理", 60)
            info = handlers[backend](cfg, opts, text, ref, out, work, cancel_event)
            if not out.is_file() or out.stat().st_size < 1024:
                raise InferenceError("推理完成但未生成有效音频文件")
            if progress is not None:
                progress("正在写入音频", 90)
            file_id = publish_output(out, {
                "backend": backend,
                "demo": backend == "mock",
                "text": text,
                "sample_mode": opts.sample_mode,
                "seed": opts.seed,
                "log": info.get("log", ""),
            }, owner=owner)
            wav_info = inspect_wav(output_dir() / f"{file_id}.wav")
            return {
                "id": file_id,
                "url": f"/v1/voice-clone/audio/{file_id}",
                "backend": backend,
                "demo": backend == "mock",
                "model": MODEL_NAME if backend != "mock" else "演示模式（未加载模型）",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "sample_rate": info.get("sample_rate", wav_info["sample_rate"]),
                "duration": wav_info["duration"],
                "channels": wav_info["channels"],
                "size": wav_info["size"],
                "text": text,
                "log": info.get("log", ""),
            }
    finally:
        # 推理临时目录（上传文件 + prompt.txt + 中间 wav）用完即清
        try:
            shutil.rmtree(work, ignore_errors=True)
        except OSError:
            logger.warning("清理临时目录失败：%s", work, exc_info=True)


def _valid_id(file_id: str) -> bool:
    return len(file_id) == 16 and all(c in "0123456789abcdef" for c in file_id)


def publish_output(
    src: Path, meta: dict[str, Any], *, owner: str = "local", file_id: str | None = None
) -> str:
    """原子写产物 + 元数据到数据目录，返回短 id；顺带做容量回收。"""
    _ensure_dirs()
    file_id = file_id or uuid.uuid4().hex[:16]
    out = output_dir()
    tmp_wav = out / f"{file_id}.wav.tmp"
    tmp_wav.write_bytes(src.read_bytes())
    os.replace(tmp_wav, out / f"{file_id}.wav")

    payload = dict(meta)
    payload.update({"id": file_id, "created_at": int(time.time()), "owner": owner})
    tmp_json = out / f"{file_id}.json.tmp"
    tmp_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_json, out / f"{file_id}.json")
    cleanup_outputs_now()
    return file_id


def _unlink_pair(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        Path(str(path) + ".tmp").unlink(missing_ok=True)
    except OSError:
        logger.warning("清理历史音频失败：%s", path, exc_info=True)


def cleanup_outputs_now() -> None:
    """容量 + 过期回收：wav/json 成对删，顺带清孤儿 json 与崩溃残留的临时目录。"""
    cfg = get_settings()
    out = output_dir()
    if not out.is_dir():
        return
    with _CLEANUP_LOCK:
        files = sorted(out.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for index, path in enumerate(files):
            too_old = now - path.stat().st_mtime > cfg.output_retention_seconds
            too_many = index >= cfg.output_max_files
            if too_old or too_many:
                _unlink_pair(path)
        for json_path in out.glob("*.json"):
            if not json_path.with_suffix(".wav").is_file() and now - json_path.stat().st_mtime > 60:
                try:
                    json_path.unlink(missing_ok=True)
                except OSError:
                    pass
        tdir = tmp_dir()
        if tdir.is_dir():
            for child in tdir.iterdir():
                if child.is_dir() and now - child.stat().st_mtime > cfg.output_retention_seconds:
                    shutil.rmtree(child, ignore_errors=True)


def remove_output(file_id: str) -> None:
    """删除指定产物（长文本分句合成用来清掉中间 wav）。"""
    if _valid_id(file_id):
        _unlink_pair(output_dir() / f"{file_id}.wav")


def _read_meta(file_id: str) -> dict[str, Any]:
    path = output_dir() / f"{file_id}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _accessible(file_id: str, owner: str | None) -> bool:
    if not _valid_id(file_id):
        return False
    path = output_dir() / f"{file_id}.wav"
    if not path.is_file():
        return False
    if time.time() - path.stat().st_mtime > get_settings().output_retention_seconds:
        return False
    if owner is not None and get_settings().auth_enabled:
        expected = _read_meta(file_id).get("owner")
        if expected and expected != owner:
            return False
    return True


def get_output_path(file_id: str, owner: str | None = None) -> Path | None:
    if not _accessible(file_id, owner):
        return None
    return output_dir() / f"{file_id}.wav"


def get_output_meta(file_id: str, owner: str | None = None) -> dict[str, Any]:
    if not _accessible(file_id, owner):
        return {}
    meta = _read_meta(file_id)
    meta.pop("owner", None)
    return meta


def remove_outputs_by_owner(owner: str) -> int:
    """删除某 owner 的全部合成产物（wav + 元数据 json），返回删除数量。

    供一键清空数据使用：auth 关闭时所有产物 owner 都是 'local'，即清空全部。
    """
    out = output_dir()
    if not out.is_dir():
        return 0
    removed = 0
    for json_path in list(out.glob("*.json")):
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if meta.get("owner") != owner:
            continue
        _unlink_pair(out / f"{json_path.stem}.wav")
        removed += 1
    return removed
