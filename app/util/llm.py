# -*- coding: UTF-8 -*-
"""大语言模型（LLM）适配层 —— 语音聊天的『大脑』。

默认用 Qwen2-0.5B（量化 GGUF，ctransformers 加载，纯 CPU 友好）。
未安装 ctransformers 或未配置权重时降级为 mock（基于关键词的中文回复），
保证链路在无权重情况下也能跑通。真实路径懒加载 + 单例。

环境变量（可选）:
  VOICE_CHAT_LLM_BACKEND  auto(默认) / qwen2 / mock
  VOICE_CHAT_QWEN_MODEL    Qwen2-0.5B 的 GGUF 文件路径
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("auto", "qwen2", "mock")
_SYSTEM_PROMPT = (
    "你是一个友好、简洁的中文语音助手，用自然口语回答用户的问题，"
    "回答控制在三句话以内，不使用 Markdown 格式。"
)
_QWEN_WRAP_RE = re.compile(r"<\|im_(?:start|end)\|>", re.IGNORECASE)


class LLMNotReady(RuntimeError):
    """LLM 引擎未就绪，附带解决指引。"""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _default_model_path() -> Path:
    raw = os.environ.get("VOICE_CHAT_QWEN_MODEL", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return PROJECT_ROOT / "models" / "qwen2-0.5b-instruct-q8_0.gguf"


def _build_prompt(messages: list[dict[str, str]]) -> str:
    """拼成 Qwen2 对话模板。messages: [{"role","content"}]。"""
    parts = [f"<|im_start|>system\n{_SYSTEM_PROMPT}<|im_end|>"]
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def _mock_reply(messages: list[dict[str, str]]) -> str:
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content", "")
            break
    text = user_text.strip()
    if any(k in text for k in ("诗", "夏天", "春", "秋", "冬")):
        return "夏天的风很轻，蝉鸣爬满黄昏，我们在树荫下，把日子过得很慢。"
    if any(k in text for k in ("大模型", "什么", "解释", "原理")):
        return "大模型就是用海量文本训练出来的神经网络，能理解和生成自然语言，像会聊天的大脑。"
    if any(k in text for k in ("书", "推荐", "读")):
        return "推荐《三体》《活着》《小王子》，一本看宇宙，一本看人生，一本看童心。"
    if any(k in text for k in ("笑话", "搞笑", "冷")):
        return "为什么企鹅只有肚子白？因为它手太短，洗澡够不着后背。"
    if any(k in text for k in ("天气", "今天", "怎么样")):
        return "今天天气不错，适合出去走走，记得多喝水，别中暑啦。"
    return f"我听到你说：{text or '（空）'}。这是一个演示回复，配置好 Qwen2-0.5B 后我就能真正和你聊天了。"


def _qwen_model() -> Any:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from ctransformers import AutoModelForCausalLM  # 仅真实后端时 import
        except ImportError as exc:
            raise LLMNotReady(
                "未安装 ctransformers，无法加载 Qwen2-0.5B",
                "pip install ctransformers 后再试，或设置 VOICE_CHAT_LLM_BACKEND=mock 走演示链路",
            ) from exc
        model_path = _default_model_path()
        if not model_path.is_file():
            raise LLMNotReady(
                f"未找到 Qwen2-0.5B 权重：{model_path}",
                f"下载 qwen2-0.5b GGUF 放到该路径，或设置 VOICE_CHAT_QWEN_MODEL 指定位置",
            )
        _MODEL = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            model_type="qwen2",
            context_length=2048,
            threads=max(1, get_settings().max_inference_slots),
        )
        logger.info("Qwen2-0.5B 已加载（ctransformers, CPU）：%s", model_path)
        return _MODEL


def _qwen_chat(messages: list[dict[str, str]]) -> str:
    model = _qwen_model()
    prompt = _build_prompt(messages)
    try:
        out = model(prompt, max_new_tokens=256, temperature=0.7, top_p=0.9, stop=["<|im_end|>"])
    except Exception as exc:
        raise LLMNotReady(f"Qwen2 推理失败：{exc}", "检查权重完整性与上下文长度") from exc
    text = _QWEN_WRAP_RE.sub("", str(out)).strip()
    return text or "（模型未生成有效回复）"


def available_backend() -> str:
    try:
        import ctransformers  # noqa: F401
    except ImportError:
        return "mock"
    return "qwen2"


def chat(messages: list[dict[str, str]], *, backend: str | None = None) -> str:
    """多轮对话。messages: [{"role":"system"|"user"|"assistant","content":...}]。"""
    backend = (backend or get_settings().voice_chat_llm_backend).strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"不支持的 LLM 后端：{backend}")
    if backend == "mock":
        return _mock_reply(messages)
    if backend == "qwen2":
        return _qwen_chat(messages)
    if available_backend() == "qwen2":
        return _qwen_chat(messages)
    return _mock_reply(messages)


def probe() -> dict[str, Any]:
    backend = available_backend()
    return {
        "model": "Qwen2-0.5B-Instruct (GGUF, ctransformers, CPU)",
        "backend": backend,
        "ready": backend == "qwen2",
        "hint": "" if backend == "qwen2" else "未安装 ctransformers 或未配置权重，当前为演示回复",
    }


_MODEL: Any = None
_MODEL_LOCK = threading.Lock()
