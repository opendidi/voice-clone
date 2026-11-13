# -*- coding: UTF-8 -*-
"""核心纯逻辑单测：WAV 校验 / 拼接 / 分句 / 档案 / 归属 / 闸门 / 限流 / 探测缓存。"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from app.util import inference_slots, long_tts, moss_tts, voice_profiles, wav_tools
from app.util import rate_limit as rl

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_validate_reference_audio(env, wav_bytes, monkeypatch):
    good = env / "good.wav"
    good.write_bytes(wav_bytes())
    info = moss_tts.validate_reference_audio(good)
    assert info["duration"] == 1.2 and info["sample_rate"] == 48000

    bad = env / "bad.wav"
    bad.write_bytes(b"not a wav")
    with pytest.raises(ValueError, match="WAV"):
        moss_tts.validate_reference_audio(bad)

    short = env / "short.wav"
    short.write_bytes(wav_bytes(0.4))
    with pytest.raises(ValueError, match="太短"):
        moss_tts.validate_reference_audio(short)

    monkeypatch.setattr(moss_tts, "MAX_REF_BYTES", 100)
    with pytest.raises(ValueError, match="过大"):
        moss_tts.validate_reference_audio(good)


def test_concat_wav(env, wav_bytes):
    a, b = env / "a.wav", env / "b.wav"
    a.write_bytes(wav_bytes(1.0))
    b.write_bytes(wav_bytes(0.5))
    out = env / "merged.wav"
    info = wav_tools.concat_wav([a, b], out, gap_seconds=0.1)
    assert info["duration"] == pytest.approx(1.6, abs=0.01)
    assert info["sample_rate"] == 48000 and out.stat().st_size > 44

    different = env / "different.wav"
    different.write_bytes(wav_bytes(0.5, rate=16000))
    with pytest.raises(ValueError, match="不一致"):
        wav_tools.concat_wav([a, different], env / "bad.wav")


def test_split_text():
    assert long_tts.split_text("第一句。第二句！第三句？") == ["第一句。", "第二句！", "第三句？"]
    long = "啊" * 300 + "。"
    pieces = long_tts.split_text(long)
    assert len(pieces) == 3 and all(len(p) <= 120 for p in pieces)
    assert long_tts.split_text("") == []


def test_profiles_isolation(env, wav_bytes):
    ref = env / "ref.wav"
    ref.write_bytes(wav_bytes())
    item = voice_profiles.save_profile("alice", "我的声音", ref)
    assert item["id"] and item["name"] == "我的声音"
    assert len(voice_profiles.list_profiles("alice")) == 1
    assert voice_profiles.get_profile_ref("alice", item["id"]) is not None
    assert voice_profiles.get_profile_ref("bob", item["id"]) is None
    assert voice_profiles.delete_profile("alice", item["id"]) is True
    assert voice_profiles.delete_profile("alice", item["id"]) is False


def test_output_owner_check(env, wav_bytes, monkeypatch):
    src = env / "src.wav"
    src.write_bytes(wav_bytes())
    file_id = moss_tts.publish_output(src, {"text": "隐私文本"}, owner="alice")
    assert moss_tts.get_output_path(file_id, "alice") is not None
    assert "owner" not in moss_tts.get_output_meta(file_id, "alice")

    monkeypatch.setenv("VOICE_CLONE_USERS", '{"key-b":"bob"}')
    assert moss_tts.get_output_path(file_id, "bob") is None
    assert moss_tts.get_output_meta(file_id, "bob") == {}
    assert moss_tts.get_output_path(file_id, "alice") is not None
    moss_tts.remove_output(file_id)
    assert moss_tts.get_output_path(file_id, "alice") is None


def test_inference_slot_exclusive(env, monkeypatch):
    monkeypatch.setenv("VOICE_CLONE_MAX_INFERENCE", "1")
    with inference_slots.inference_slot(wait_seconds=1):
        with pytest.raises(inference_slots.SlotsBusy):
            with inference_slots.inference_slot(wait_seconds=0):
                pass
    # 释放后可以再次占用
    with inference_slots.inference_slot(wait_seconds=1):
        pass


def test_probe_cache(env, monkeypatch):
    monkeypatch.setenv("VOICE_CLONE_PROBE_CACHE", "60")
    calls = {"n": 0}

    def fake_probe():
        calls["n"] += 1
        return {"backend": "mock", "ready": True}

    monkeypatch.setattr(moss_tts, "_probe_uncached", fake_probe)
    moss_tts.invalidate_probe_cache()
    assert moss_tts.probe()["backend"] == "mock"
    assert moss_tts.probe()["backend"] == "mock"
    assert calls["n"] == 1
    moss_tts.probe(force=True)
    assert calls["n"] == 2


def test_rate_limit_sliding_window(flask_app):
    @rl.rate_limit(2, 60)
    def handler():
        return "ok"

    with flask_app.test_request_context("/"):
        assert handler() == "ok"
        assert handler() == "ok"
        blocked = handler()
        assert isinstance(blocked, tuple) and blocked[1] == 429
    rl.reset_counters()
    with flask_app.test_request_context("/"):
        assert handler() == "ok"


def test_settings_users_and_cors(env, monkeypatch):
    from app.config.settings import get_settings

    monkeypatch.setenv("VOICE_CLONE_USERS", '{"k1":"alice","k2":"bob"}')
    monkeypatch.setenv("VOICE_CLONE_CORS_ORIGINS", "https://a.example,https://b.example")
    cfg = get_settings()
    assert cfg.auth_enabled and cfg.api_users == {"k1": "alice", "k2": "bob"}
    assert cfg.cors_origins == ["https://a.example", "https://b.example"]
    assert not cfg.allow_backend_override


def test_profile_path_traversal_is_blocked(env, wav_bytes):
    root = voice_profiles.profiles_root().resolve()
    for uid in ("..", ".", "a..b", "../../etc", "/tmp/evil"):
        target = voice_profiles._user_dir(uid)
        assert target == root or root in target.parents
        assert ".." not in target.name

    ref = env / "ref.wav"
    ref.write_bytes(wav_bytes())
    item = voice_profiles.save_profile("alice", "正常音色", ref)

    assert voice_profiles.delete_profile("alice", "../../etc/passwd") is False
    assert voice_profiles.get_profile_ref("alice", "../secret") is None

    # 即使 index.json 被篡改，也不能借 file 字段读到目录外的文件
    index = voice_profiles._index_path("alice")
    data = json.loads(index.read_text(encoding="utf-8"))
    data[0]["file"] = "../../../etc/passwd"
    index.write_text(json.dumps(data), encoding="utf-8")
    assert voice_profiles.get_profile_ref("alice", item["id"]) is None


def test_cli_cancel_terminates_process(env, monkeypatch):
    """CLI 后端必须能响应取消并终止子进程，同时强制 UTF-8 读取中文 prompt。"""
    (env / "infer_onnx.py").write_text("# fake\n", encoding="utf-8")
    work = env / "work"
    work.mkdir()
    ref = env / "ref.wav"
    ref.write_bytes(b"RIFF")
    out = work / "out.wav"

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.terminated = False
            self.returncode = 1
            self.pid = 4242

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.terminated = True

    created = []

    def fake_popen(*args, **kwargs):
        proc = FakeProc(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(moss_tts.subprocess, "Popen", fake_popen)
    cfg = moss_tts.MossConfig(repo_dir=env, python_bin=sys.executable, timeout=30)
    cancel = threading.Event()
    threading.Timer(0.15, cancel.set).start()

    with pytest.raises(moss_tts.InferenceError, match="取消"):
        moss_tts._synthesize_cli(
            cfg, moss_tts.SynthOptions(), "你好", ref, out, work, cancel
        )
    assert created and created[0].terminated
    assert created[0].kwargs["env"]["PYTHONUTF8"] == "1"


def test_probe_setup_commands_use_absolute_requirements(env):
    status = moss_tts.probe(force=True)
    joined = "\n".join(status["setup_commands"])
    assert str(PROJECT_ROOT / "requirements-moss-tts.txt") in joined
    assert "-m pip install -r" in joined
