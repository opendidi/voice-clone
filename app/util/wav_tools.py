# -*- coding: UTF-8 -*-
"""WAV 拼接工具（只用标准库 wave，不引入音频依赖）。"""
from __future__ import annotations

import wave
from pathlib import Path


def concat_wav(paths: list[Path], out: Path, gap_seconds: float = 0.12) -> dict:
    """把同参数的 PCM WAV 顺序拼接成单个文件，句间插入一小段静音。

    不同后端返回的采样率/声道若不一致直接报错，不做隐式重采样，
    避免拼接出变速或错位的音频。
    """
    if not paths:
        raise ValueError("没有可拼接的音频片段")

    with wave.open(str(paths[0]), "rb") as first:
        channels = first.getnchannels()
        width = first.getsampwidth()
        rate = first.getframerate()
        comptype = first.getcomptype()

    for path in paths:
        with wave.open(str(path), "rb") as src:
            if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (
                channels, width, rate, comptype
            ):
                raise ValueError(f"音频参数不一致，无法拼接：{path.name}")

    silence = b"\x00" * (int(rate * gap_seconds) * channels * width)
    out.parent.mkdir(parents=True, exist_ok=True)
    total_frames = 0
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(width)
        dst.setframerate(rate)
        dst.setcomptype(comptype, "not compressed")
        for index, path in enumerate(paths):
            if index:
                dst.writeframes(silence)
                total_frames += int(rate * gap_seconds)
            with wave.open(str(path), "rb") as src:
                frames = src.readframes(src.getnframes())
                dst.writeframes(frames)
                total_frames += src.getnframes()

    return {
        "sample_rate": rate,
        "channels": channels,
        "sample_width": width,
        "duration": round(total_frames / float(rate), 3) if rate else 0.0,
        "size": out.stat().st_size,
    }
