# -*- coding: UTF-8 -*-
"""接口集成测试（mock 后端，不加载 ONNX 权重）。"""
from __future__ import annotations

import importlib.util
import io
import time
from pathlib import Path


def wait_job(client, job_id: str, timeout: float = 15, headers: dict | None = None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(
            f"/v1/voice-clone/jobs/{job_id}", headers=headers or {}
        ).get_json()["data"]
        if body["status"] in ("succeeded", "failed", "canceled"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未结束")


def upload(client, wav_bytes, text: str = "你好，这是测试。", backend: str | None = None):
    data = {"text": text, "audio": (io.BytesIO(wav_bytes()), "ref.wav")}
    if backend:
        data["backend"] = backend
    return client.post(
        "/v1/voice-clone/jobs", data=data, content_type="multipart/form-data"
    )


def test_routes_and_health(flask_app, client):
    rules = {str(r) for r in flask_app.url_map.iter_rules()}
    assert "/v1/voice-clone/jobs" in rules
    assert "/v1/voice-clone/jobs/<job_id>" in rules
    assert "/v1/voice-clone/tts/segment" in rules
    assert not any(rule.startswith("/static/") for rule in rules)
    body = client.get("/health").get_json()
    assert body["ok"] and body["service"] == "voice-clone"
    assert "auth_enabled" in body and "max_inference_slots" in body


def test_clone_job_flow(client, wav_bytes):
    created = upload(client, wav_bytes)
    assert created.status_code == 202
    job = created.get_json()["data"]
    assert job["status"] in ("queued", "running")

    final = wait_job(client, job["id"])
    assert final["status"] == "succeeded", final["error"]
    result = final["result"]
    assert result["demo"] is True and result["backend"] == "mock"
    assert result["reference"]["duration"] >= 1.0

    file_id = result["id"]
    audio = client.get(f"/v1/voice-clone/audio/{file_id}")
    assert audio.status_code == 200 and audio.data[:4] == b"RIFF"
    assert client.get(f"/v1/voice-clone/meta/{file_id}").status_code == 200
    assert client.get("/v1/voice-clone/audio/notavalidid0000").status_code == 404


def test_profiles_and_long_tts_job(client, wav_bytes):
    saved = client.post(
        "/v1/voice-clone/profiles",
        data={"name": "测试音色", "audio": (io.BytesIO(wav_bytes()), "ref.wav")},
        content_type="multipart/form-data",
    )
    assert saved.status_code == 200
    pid = saved.get_json()["data"]["profile"]["id"]
    assert len(client.get("/v1/voice-clone/profiles").get_json()["data"]["profiles"]) == 1

    created = client.post("/v1/voice-clone/jobs", json={
        "text": "第一句。第二句也要读出来！", "profile_id": pid, "long": True,
    })
    assert created.status_code == 202
    final = wait_job(client, created.get_json()["data"]["id"])
    assert final["status"] == "succeeded", final["error"]
    assert final["result"]["segments"] == 2
    assert final["result"]["duration"] > 2

    assert client.delete(f"/v1/voice-clone/profiles/{pid}").status_code == 200
    assert client.delete(f"/v1/voice-clone/profiles/{pid}").status_code == 404


def test_cancel_job(client, wav_bytes):
    created = upload(client, wav_bytes)
    job_id = created.get_json()["data"]["id"]
    assert client.delete(f"/v1/voice-clone/jobs/{job_id}").status_code == 200
    final = wait_job(client, job_id)
    assert final["status"] in ("canceled", "succeeded")
    assert client.delete("/v1/voice-clone/jobs/nonexistent").status_code == 404


def test_backend_override_is_ignored(client, wav_bytes):
    """默认由服务端决定后端；前端只有 mock 演示模式可以切换。"""
    resp = client.post(
        "/v1/voice-clone/clone",
        data={
            "text": "你好", "backend": "onnx",
            "audio": (io.BytesIO(wav_bytes()), "ref.wav"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["backend"] == "mock"


def test_auth_and_user_isolation(client, wav_bytes, monkeypatch):
    monkeypatch.setenv("VOICE_CLONE_USERS", '{"key-a":"alice","key-b":"bob"}')
    headers_a = {"Authorization": "Bearer key-a"}
    headers_b = {"Authorization": "Bearer key-b"}

    assert client.get("/v1/voice-clone/profiles").status_code == 401
    assert client.get(
        "/v1/voice-clone/profiles", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.get("/v1/voice-clone/profiles", headers=headers_a).status_code == 200

    # X-User-Id 不再被信任：即使伪造 bob，档案仍归 alice
    saved = client.post(
        "/v1/voice-clone/profiles",
        data={"name": "alice 的声音", "audio": (io.BytesIO(wav_bytes()), "ref.wav")},
        content_type="multipart/form-data",
        headers={**headers_a, "X-User-Id": "bob"},
    )
    assert saved.status_code == 200
    profiles_b = client.get("/v1/voice-clone/profiles", headers=headers_b).get_json()["data"]["profiles"]
    assert profiles_b == []

    # 未带 key 直接拒绝；带 alice 的 key 正常提交，产物归 alice 所有
    assert upload(client, wav_bytes).status_code == 401
    auth_job = client.post(
        "/v1/voice-clone/jobs",
        data={"text": "你好", "audio": (io.BytesIO(wav_bytes()), "ref.wav")},
        content_type="multipart/form-data",
        headers=headers_a,
    )
    assert auth_job.status_code == 202
    final = wait_job(client, auth_job.get_json()["data"]["id"], headers=headers_a)
    assert final["status"] == "succeeded", final["error"]
    fid = final["result"]["id"]
    assert client.get(f"/v1/voice-clone/audio/{fid}", headers=headers_a).status_code == 200
    assert client.get(f"/v1/voice-clone/audio/{fid}", headers=headers_b).status_code == 404


def test_cors_whitelist(client):
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    blocked = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "Access-Control-Allow-Origin" not in blocked.headers
    assert allowed.headers.get("X-Request-Id")


def test_wsgi_entrypoint_exports_flask_app(env):
    """回归：根目录 app.py 与 app/ 包同名，生产入口必须按文件路径加载。"""
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("vc_wsgi_test", root / "wsgi.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod.app, "test_client")
    assert callable(mod.start_maintenance)


def test_framework_errors_are_envelopes(flask_app, client, wav_bytes):
    r404 = client.get("/v1/voice-clone/not-a-route")
    assert r404.status_code == 404 and r404.get_json()["code"] == 404
    r405 = client.post("/v1/voice-clone/status")
    assert r405.status_code == 405 and r405.get_json()["code"] == 405

    # 触发 Flask 的 413（请求体超限），响应也必须能被前端 unwrap 解开
    flask_app.config["MAX_CONTENT_LENGTH"] = 10
    r413 = client.post(
        "/v1/voice-clone/jobs",
        data={"text": "payload exceeds the tiny limit", "audio": (io.BytesIO(wav_bytes()), "ref.wav")},
        content_type="multipart/form-data",
    )
    assert r413.status_code == 413 and r413.get_json()["code"] == 413


def test_delete_audio_and_preview_profile(client, wav_bytes):
    created = upload(client, wav_bytes)
    final = wait_job(client, created.get_json()["data"]["id"])
    assert final["status"] == "succeeded"
    fid = final["result"]["id"]
    assert client.get(f"/v1/voice-clone/audio/{fid}").status_code == 200
    deleted = client.delete(f"/v1/voice-clone/audio/{fid}")
    assert deleted.status_code == 200 and deleted.get_json()["data"]["deleted"] is True
    assert client.get(f"/v1/voice-clone/audio/{fid}").status_code == 404
    assert client.get(f"/v1/voice-clone/meta/{fid}").status_code == 404

    saved = client.post(
        "/v1/voice-clone/profiles",
        data={"name": "试听音色", "audio": (io.BytesIO(wav_bytes()), "ref.wav")},
        content_type="multipart/form-data",
    )
    pid = saved.get_json()["data"]["profile"]["id"]
    preview = client.get(f"/v1/voice-clone/profiles/{pid}/audio")
    assert preview.status_code == 200 and preview.data[:4] == b"RIFF"
    assert client.get("/v1/voice-clone/profiles/not-a-pid/audio").status_code == 404


def test_health_does_not_leak_local_paths(client):
    body = client.get("/health").get_json()
    assert body["ok"] and "data_dir" not in body
