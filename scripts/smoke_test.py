# -*- coding: UTF-8 -*-
"""独立项目自检：导入链路、路由注册、健康检查（不加载模型、不发推理请求）。

用法：.venv-moss/Scripts/python.exe scripts/smoke_test.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("vc_app", ROOT / "app.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

rules = sorted(str(r) for r in mod.app.url_map.iter_rules())
print("registered routes:")
for r in rules:
    print("  ", r)

expected = [
    "/health",
    "/v1/voice-clone/status",
    "/v1/voice-clone/jobs",
    "/v1/voice-clone/jobs/<job_id>",
    "/v1/voice-clone/clone",
    "/v1/voice-clone/meta/<file_id>",
    "/v1/voice-clone/audio/<file_id>",
    "/v1/voice-clone/profiles",
    "/v1/voice-clone/profiles/<pid>",
    "/v1/voice-clone/tts",
    "/v1/voice-clone/tts/segment",
]
for path in expected:
    assert path in rules, f"missing route: {path}"

# 声纹 / 产物目录不能被 Flask 静态路由暴露
assert not any(r.startswith("/static/") for r in rules), "static route must be disabled"

client = mod.app.test_client()
resp = client.get("/health")
assert resp.status_code == 200 and resp.get_json()["ok"] is True

# 推理侧依赖链（只 import，不加载 728MB 权重）
repo = ROOT / "third_party" / "MOSS-TTS-Nano"
if repo.is_dir():
    sys.path.insert(0, str(repo))
    try:
        import onnx_tts_runtime  # noqa: F401
        print("inference runtime import OK")
    except Exception as exc:
        print(f"inference runtime import failed: {exc}")

print("smoke test OK")
