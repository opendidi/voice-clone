# -*- coding: UTF-8 -*-
"""测试夹具：每个用例独立的数据目录 + mock 后端，不碰真实权重和 var/。"""
from __future__ import annotations

import importlib.util
import io
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_CLONE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOSS_TTS_BACKEND", "mock")
    monkeypatch.setenv("MOSS_TTS_SERVICE_URL", "")
    monkeypatch.setenv("VOICE_CLONE_PROBE_CACHE", "0")
    monkeypatch.setenv("VOICE_CLONE_SLOT_WAIT", "1")
    monkeypatch.delenv("VOICE_CLONE_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_CLONE_USERS", raising=False)
    monkeypatch.delenv("VOICE_CLONE_ALLOW_BACKEND_OVERRIDE", raising=False)
    monkeypatch.delenv("MOSS_BRIDGE_TOKEN", raising=False)
    return tmp_path


@pytest.fixture
def wav_bytes():
    def _make(seconds: float = 1.2, rate: int = 48000, channels: int = 1) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * int(seconds * rate) * channels)
        return buf.getvalue()

    return _make


@pytest.fixture
def flask_app(env):
    spec = importlib.util.spec_from_file_location("vc_app_test", ROOT / "app.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from app.util import jobs, moss_tts
    from app.util.rate_limit import reset_counters

    moss_tts.invalidate_probe_cache()
    jobs.reset_jobs()
    reset_counters()
    yield mod.app
    jobs.reset_jobs()


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()
