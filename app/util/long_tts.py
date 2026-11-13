# -*- coding: UTF-8 -*-
"""长文本播报：自动分句 -> 逐句克隆合成 -> 拼接成一个 WAV。

整个长任务只占用一个推理槽位（slot_already_held=True 传给逐句合成），
避免每句重新排队，也避免一个长任务把并发闸门全部占满。
"""
from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.util import inference_slots, moss_tts, wav_tools

SEGMENT_MAX_CHARS = 120
_SPLIT_RE = re.compile(r"([。！？!?；;\n]+)")


def split_text(text: str, max_chars: int = SEGMENT_MAX_CHARS) -> list[str]:
    """按中英文句末标点断句；超长句再按逗号或字数硬切。"""
    pieces = _SPLIT_RE.split(text)
    sentences: list[str] = []
    buffer = ""
    for index in range(0, len(pieces), 2):
        buffer += pieces[index]
        if index + 1 < len(pieces):
            buffer += pieces[index + 1]
        if buffer.strip():
            sentences.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        sentences.append(buffer.strip())

    result: list[str] = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = max(sentence.rfind("，", 0, max_chars), sentence.rfind(",", 0, max_chars))
            cut = cut + 1 if cut >= max_chars // 2 else max_chars
            result.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            result.append(sentence)
    return result


def synthesize_long(
    text: str,
    ref: Path,
    *,
    opts: moss_tts.SynthOptions,
    owner: str,
    cancel_event: threading.Event,
    report: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    """返回与 /clone 一致的 CloneResult，附带 segments 句数。"""
    sentences = split_text(text)
    if not sentences:
        raise ValueError("没有可合成的文本")
    total = len(sentences)
    started = time.perf_counter()

    def _report(stage: str, percent: int, detail: str = "") -> None:
        if report is not None:
            report(stage, percent, detail)

    work = moss_tts.tmp_dir() / f"long-{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        with inference_slots.inference_slot(cancel_event=cancel_event):
            _report("分句完成", 20, f"共 {total} 句")
            for index, sentence in enumerate(sentences):
                if cancel_event.is_set():
                    raise moss_tts.InferenceError("任务已取消")
                _report(f"合成第 {index + 1}/{total} 句", 20 + int(60 * index / total))
                item = moss_tts.synthesize(
                    sentence,
                    ref,
                    opts,
                    owner=owner,
                    cancel_event=cancel_event,
                    slot_already_held=True,
                )
                src = moss_tts.get_output_path(item["id"], owner)
                if src is None:
                    raise moss_tts.InferenceError("句子产物写入失败")
                segment_path = work / f"seg-{index:04d}.wav"
                shutil.copyfile(src, segment_path)
                paths.append(segment_path)
                moss_tts.remove_output(item["id"])

            _report("正在拼接音频", 90)
            merged = work / "merged.wav"
            info = wav_tools.concat_wav(paths, merged)
            file_id = moss_tts.publish_output(
                merged,
                {
                    "backend": item.get("backend", ""),
                    "demo": bool(item.get("demo")),
                    "text": text,
                    "log": f"长文本播报：{total} 句已拼接",
                    "segments": total,
                },
                owner=owner,
            )
        return {
            "id": file_id,
            "url": f"/v1/voice-clone/audio/{file_id}",
            "backend": item.get("backend", ""),
            "demo": bool(item.get("demo")),
            "model": item.get("model", ""),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "sample_rate": info["sample_rate"],
            "duration": info["duration"],
            "channels": info["channels"],
            "size": info["size"],
            "text": text,
            "log": f"长文本播报：{total} 句已拼接",
            "segments": total,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
