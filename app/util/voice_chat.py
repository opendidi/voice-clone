# -*- coding: UTF-8 -*-
"""语音聊天流水线编排：ASR(听) -> LLM(想) -> TTS(说)。

复用成熟组件：
  * ASR  —— app/util/asr.py（SenseVoice-small，缺依赖则 mock）
  * LLM  —— app/util/llm.py（Qwen2-0.5B，缺依赖则 mock）
  * TTS  —— 复用 MOSS-TTS-Nano（app/util/moss_tts.py），产物照常走 owner 隔离
语音聊天是『一问一答』短任务，复用 jobs 异步链路做阶段进度（听写中/思考中/合成中）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.config.settings import get_settings
from app.util import asr, llm, moss_tts, voice_profiles

logger = logging.getLogger(__name__)

Report = Callable[[str, int, str], None]


def resolve_reference(uid: str, profile_id: str | None) -> Path:
    """选定语音聊天的参考音色（按优先级）：
    显式指定 -> 用户首个音色 -> 打包默认音色 -> 提示用户先存一个。
    """
    if profile_id:
        ref = voice_profiles.get_profile_ref(uid, profile_id)
        if ref is not None:
            return ref
        raise ValueError("指定的语音档案不存在或已删除")

    profiles = voice_profiles.list_profiles(uid)
    if profiles:
        ref = voice_profiles.get_profile_ref(uid, profiles[0]["id"])
        if ref is not None:
            return ref

    default_ref = get_settings().voice_chat_default_ref
    if default_ref.is_file():
        return default_ref

    raise ValueError("请先在『音色库』保存或选择一个音色，或配置 VOICE_CHAT_DEFAULT_REF 指定默认参考音频")


def run_pipeline(
    audio_path: Path,
    uid: str,
    profile_id: str | None,
    report: Report | None = None,
) -> dict[str, Any]:
    """跑完一次语音聊天回合，返回 {transcript, reply, audio, backends}。"""
    def step(stage: str, percent: int, detail: str = "") -> None:
        if report is not None:
            report(stage, percent, detail)

    # 1) 听写
    step("正在听写", 15)
    transcript = asr.transcribe(audio_path)
    step("听写完成", 35)

    # 2) 思考
    step("正在思考", 45)
    reply = llm.chat([{"role": "user", "content": transcript}])
    step("思考完成", 70)

    # 3) 合成（复用 MOSS-TTS，内部自行占用推理槽位并发布产物）
    step("正在合成", 80)
    ref = resolve_reference(uid, profile_id)
    tts_result = moss_tts.synthesize(
        reply, ref, moss_tts.SynthOptions(), owner=uid,
        progress=lambda stage, percent: step(f"正在合成·{stage}", 80 + int(percent * 0.18)),
    )
    step("合成完成", 100)

    return {
        "transcript": transcript,
        "reply": reply,
        "audio": {
            "id": tts_result["id"],
            "url": tts_result["url"],
            "demo": tts_result["demo"],
            "duration": tts_result.get("duration", 0),
            "sample_rate": tts_result.get("sample_rate", 0),
        },
        "backends": {
            "asr": asr.available_backend(),
            "llm": llm.available_backend(),
            "tts": tts_result.get("backend", "unknown"),
        },
    }
