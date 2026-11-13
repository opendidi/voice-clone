# -*- coding: UTF-8 -*-
"""集中式运行配置。

所有环境变量只在这里解析，其余模块一律通过 get_settings() 读取，
避免配置散落在各个文件里、也方便测试用 monkeypatch 覆盖。
另外把 .env 载入放在此处，保证 app.py 之后 import 的任何模块都拿得到配置。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """轻量 .env 读取，不引入 python-dotenv；已存在的环境变量优先。"""
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _api_users() -> dict[str, str]:
    """API Key -> uid 映射。多人使用时用 VOICE_CLONE_USERS 配置。

    单 key 模式（VOICE_CLONE_API_KEY）统一映射到 local，保持向后兼容。
    """
    users: dict[str, str] = {}
    raw = os.environ.get("VOICE_CLONE_USERS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                users = {
                    str(k).strip(): str(v).strip()[:64]
                    for k, v in parsed.items()
                    if str(k).strip() and str(v).strip()
                }
        except (ValueError, TypeError):
            # 配置写错不应让服务起不来，退化成单 key 模式并在 health 里提示
            users = {}
    single = os.environ.get("VOICE_CLONE_API_KEY", "").strip()
    if single:
        users.setdefault(single, "local")
    return users


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    output_dir: Path
    profiles_dir: Path
    lock_dir: Path
    api_users: dict[str, str]
    cors_origins: list[str]
    allow_backend_override: bool
    max_inference_slots: int
    slot_wait_seconds: int
    slot_lease_seconds: int
    probe_cache_seconds: int
    job_ttl_seconds: int
    job_max: int
    output_retention_seconds: int
    output_max_files: int
    long_tts_max_chars: int
    bridge_token: str
    log_level: str

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_users)

    @property
    def cors_allow_all(self) -> bool:
        return "*" in self.cors_origins


DEFAULT_CORS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:5002,http://127.0.0.1:5002"
)


def get_settings() -> Settings:
    """每次调用重新读取环境变量，本地调试改配置无需重启。"""
    data_dir = Path(
        os.environ.get("VOICE_CLONE_DATA_DIR", "").strip() or (PROJECT_ROOT / "var")
    ).expanduser().resolve()
    return Settings(
        data_dir=data_dir,
        output_dir=data_dir / "voice-clone",
        profiles_dir=data_dir / "voice-profiles",
        lock_dir=data_dir / "locks",
        api_users=_api_users(),
        cors_origins=_csv("VOICE_CLONE_CORS_ORIGINS", DEFAULT_CORS),
        allow_backend_override=_bool("VOICE_CLONE_ALLOW_BACKEND_OVERRIDE", False),
        max_inference_slots=_int("VOICE_CLONE_MAX_INFERENCE", 2, 1, 8),
        slot_wait_seconds=_int("VOICE_CLONE_SLOT_WAIT", 30, 1, 600),
        slot_lease_seconds=_int("VOICE_CLONE_SLOT_LEASE", 900, 60, 7200),
        probe_cache_seconds=_int("VOICE_CLONE_PROBE_CACHE", 8, 0, 300),
        job_ttl_seconds=_int("VOICE_CLONE_JOB_TTL", 3600, 60, 86400),
        job_max=_int("VOICE_CLONE_JOB_MAX", 200, 10, 5000),
        output_retention_seconds=_int("VOICE_CLONE_RETENTION", 3600, 60, 86400),
        output_max_files=_int("VOICE_CLONE_MAX_FILES", 200, 10, 10000),
        long_tts_max_chars=_int("VOICE_CLONE_LONG_TTS_MAX_CHARS", 5000, 200, 50000),
        bridge_token=os.environ.get("MOSS_BRIDGE_TOKEN", "").strip(),
        log_level=os.environ.get("VOICE_CLONE_LOG_LEVEL", "INFO").strip().upper(),
    )
