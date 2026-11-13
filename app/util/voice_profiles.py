# -*- coding: UTF-8 -*-
"""语音档案存储：把一段参考音频存成「命名音色」，供文本播报按 id 复用。

档案按用户隔离，落在 <数据目录>/voice-profiles/<uid>/（默认 <项目根>/var/）：
  index.json        该用户全部档案的元信息列表
  <profile_id>.wav  参考音频（已归一化为 16-bit PCM WAV）

数据目录不是静态资源目录，禁止被 Web 服务器直接托管（声纹属于生物特征数据）。
MOSS-TTS-Nano 是 zero-shot，不导出"声纹模型"文件，所谓"克隆音源"就是这段参考音频；
每次合成都把「文本 + 参考音频」一起喂模型重算，因此档案存的是参考音频本身。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from app.config.settings import get_settings
from app.util import moss_tts

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 40

# index.json 的读改写全程持锁：保存 / 删除并发时不会互相覆盖丢档案。
_INDEX_LOCK = threading.RLock()


def profiles_root() -> Path:
    return get_settings().profiles_dir


def _user_dir(uid: str) -> Path:
    """用户目录：uid 做一层安全清洗，避免路径穿越。"""
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_@.")
    d = profiles_root() / (safe or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(uid: str) -> Path:
    return _user_dir(uid) / "index.json"


def _read_index(uid: str) -> list[dict]:
    p = _index_path(uid)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def _write_index(uid: str, items: list[dict]) -> None:
    """原子写：先落 .tmp 再 os.replace，进程中断 / 并发写都不会留下半个 JSON。"""
    p = _index_path(uid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def save_profile(uid: str, name: str, src_ref: Path) -> dict:
    """存一段参考音频为命名音色。src_ref 需为已校验的 WAV。"""
    name = (name or "").strip()[:MAX_NAME_LEN] or f"音色 {time.strftime('%m-%d %H:%M')}"
    info = moss_tts.validate_reference_audio(src_ref)
    pid = uuid.uuid4().hex[:12]
    udir = _user_dir(uid)
    target = udir / f"{pid}.wav"
    tmp = udir / f"{pid}.wav.tmp"
    shutil.copyfile(src_ref, tmp)
    os.replace(tmp, target)
    item = {
        "id": pid,
        "name": name,
        "file": f"{pid}.wav",
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "duration": info["duration"],
        "size": target.stat().st_size,
        "created_at": int(time.time()),
    }
    with _INDEX_LOCK:
        items = [it for it in _read_index(uid) if it.get("id") != item["id"]]
        items.append(item)
        _write_index(uid, items)
    return item


def list_profiles(uid: str) -> list[dict]:
    return sorted(_read_index(uid), key=lambda x: x.get("created_at", 0), reverse=True)


def get_profile(uid: str, pid: str) -> dict | None:
    for it in _read_index(uid):
        if it.get("id") == pid:
            return it
    return None


def get_profile_ref(uid: str, pid: str) -> Path | None:
    it = get_profile(uid, pid)
    if not it:
        return None
    p = _user_dir(uid) / it["file"]
    return p if p.is_file() else None


def delete_profile(uid: str, pid: str) -> bool:
    with _INDEX_LOCK:
        before = _read_index(uid)
        items = [it for it in before if it.get("id") != pid]
        if len(items) == len(before):
            return False
        _write_index(uid, items)
    (_user_dir(uid) / f"{pid}.wav").unlink(missing_ok=True)
    return True
