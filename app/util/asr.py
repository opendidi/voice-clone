# -*- coding: UTF-8 -*-
"""语音识别（ASR）适配层 —— 语音聊天的『耳朵』。

默认用阿里 SenseVoice-small（FunASR，纯 CPU 友好，支持中英+多语种+情感/事件标签）。
未安装 funasr 或未下载权重时，自动降级为 mock（返回固定中文问句），
保证前后端链路在无权重情况下也能跑通。真实路径用懒加载 + 单例，避免重复占内存。

环境变量（可选）:
  VOICE_CHAT_ASR_BACKEND  auto(默认) / sensevoice / mock
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("auto", "sensevoice", "mock")
# SenseVoice 会在文本里塞 <|zh|> ... <|NEUTRAL|> 这类标签，统一清掉
_TAG_RE = re.compile(r"<\|[^|]+\|>")


class ASRNotReady(RuntimeError):
    """ASR 引擎未就绪，附带面向用户的解决指引。"""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _mock_lines() -> list[str]:
    return [
        "你好，今天天气怎么样？",
        "帮我写一段关于夏天的短诗。",
        "用一句话解释什么是大模型。",
        "推荐三本值得读的书。",
        "讲个冷笑话听听。",
    ]


def _mock_transcribe(audio_path: Path) -> str:
    # 用文件 mtime 轮转，避免每次都返回同一句，演示更自然
    idx = int((audio_path.stat().st_mtime if audio_path.is_file() else time.time()) % len(_mock_lines()))
    line = _mock_lines()[idx]
    logger.info("ASR(mock)：返回演示文本 %r", line)
    return line


def _sensevoice_model() -> Any:
    """懒加载并缓存 SenseVoiceSmall 单例（线程安全）。"""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from funasr import AutoModel  # 仅在使用真实后端时 import
        except ImportError as exc:
            raise ASRNotReady(
                "未安装 funasr，无法使用 SenseVoice-small 真实识别",
                "pip install funasr 后再试，或设置 VOICE_CHAT_ASR_BACKEND=mock 走演示链路",
            ) from exc
        _MODEL = AutoModel(
            model="iic/SenseVoiceSmall",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            disable_update=True,
            device="cpu",
            ncpu=max(1, get_settings().max_inference_slots),
        )
        logger.info("SenseVoice-small 已加载（CPU）")
        return _MODEL


def _sensevoice_transcribe(audio_path: Path) -> str:
    model = _sensevoice_model()
    try:
        res = model.generate(
            input=str(audio_path),
            language="auto",
            use_itn=True,
            batch_size_s=60,
        )
    except Exception as exc:  # 模型推理失败不应让服务崩，降级提示
        raise ASRNotReady(f"SenseVoice 推理失败：{exc}", "检查音频格式或模型完整性") from exc
    text = (res[0].get("text") or "") if res else ""
    text = _TAG_RE.sub("", text).strip()
    return text or "（未能识别出有效内容）"


def available_backend() -> str:
    """探测当前实际可用的后端：sensevoice / mock。"""
    try:
        import funasr  # noqa: F401
    except ImportError:
        return "mock"
    return "sensevoice"


def transcribe(audio_path: Path, *, backend: str | None = None) -> str:
    """把音频转写成文本。backend 为 None 时按配置（auto 自动选可用后端）。"""
    backend = (backend or get_settings().voice_chat_asr_backend).strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"不支持的 ASR 后端：{backend}")
    if backend == "mock":
        return _mock_transcribe(audio_path)
    if backend == "sensevoice":
        return _sensevoice_transcribe(audio_path)
    # auto：优先真实，缺失则 mock
    if available_backend() == "sensevoice":
        return _sensevoice_transcribe(audio_path)
    return _mock_transcribe(audio_path)


def probe() -> dict[str, Any]:
    """能力探测，供前端展示。"""
    backend = available_backend()
    return {
        "model": "SenseVoice-small (FunASR, CPU)",
        "backend": backend,
        "ready": backend == "sensevoice",
        "hint": "" if backend == "sensevoice" else "未安装 funasr，当前为演示识别；pip install funasr 启用真实识别",
    }


_MODEL: Any = None
_MODEL_LOCK = threading.Lock()
