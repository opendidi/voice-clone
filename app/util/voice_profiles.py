# -*- coding: UTF-8 -*-
"""语音档案存储：把一段参考音频存成「命名音色」，供文本播报按 id 复用。

档案按用户隔离，落在 <数据目录>/voice-profiles/<uid>/（默认 <项目根>/var/）：
  元数据存 SQLite（app/util/db.py 的 profiles 表），本文件只保留大文件：
  <profile_id>.wav  参考音频（已归一化为 16-bit PCM WAV）

数据目录不是静态资源目录，禁止被 Web 服务器直接托管（声纹属于生物特征数据）。
MOSS-TTS-Nano 是 zero-shot，不导出"声纹模型"文件，所谓"克隆音源"就是这段参考音频；
每次合成都把「文本 + 参考音频」一起喂模型重算，因此档案存的是参考音频本身。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from app.config.settings import get_settings
from app.util import db, moss_tts

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 40
# 档案 id 与文件名都严格限定为 12 位小写 hex + .wav，杜绝 URL / index.json 带来的路径穿越
_PID_RE = re.compile(r"^[0-9a-f]{12}$")
_FILE_RE = re.compile(r"^[0-9a-f]{12}\.wav$")

def profiles_root() -> Path:
    return get_settings().profiles_dir


def _user_dir(uid: str) -> Path:
    """用户目录：清洗 uid 并校验解析后的路径仍落在 profiles_root 内。

    仅做字符过滤不够：`..`、`.`、`a..b` 里的点都是允许字符，会把目录拼到
    数据目录之外。这里额外去掉首尾点、拒绝连续点，并用 resolve() 复核。
    """
    root = profiles_root().resolve()
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_@.").strip(".")
    if not safe or ".." in safe:
        safe = "unknown"
    target = (root / safe).resolve()
    if target != root and root not in target.parents:
        target = root / "unknown"
    target.mkdir(parents=True, exist_ok=True)
    return target


# 声纹元数据统一存 SQLite（见 app/util/db.py）；大文件 wav 仍落本地目录，不进库。


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
    item["uid"] = uid
    db.upsert_profile(item)
    return item


def list_profiles(uid: str) -> list[dict]:
    return db.list_profiles(uid)


def _valid_pid(pid: str) -> bool:
    return bool(_PID_RE.fullmatch(pid or ""))


def get_profile(uid: str, pid: str) -> dict | None:
    if not _valid_pid(pid):
        return None
    return db.get_profile(uid, pid)


def get_profile_ref(uid: str, pid: str) -> Path | None:
    it = get_profile(uid, pid)
    if not it:
        return None
    filename = str(it.get("file") or "")
    if not _FILE_RE.fullmatch(filename):
        logger.warning("跳过非法档案文件名：%s", filename)
        return None
    udir = _user_dir(uid).resolve()
    p = (udir / filename).resolve()
    if udir not in p.parents:
        return None
    return p if p.is_file() else None


def delete_profile(uid: str, pid: str) -> bool:
    if not _valid_pid(pid):
        return False
    ok = db.delete_profile(uid, pid)
    (_user_dir(uid) / f"{pid}.wav").unlink(missing_ok=True)
    return ok


def purge_owner_files(uid: str) -> None:
    """清空该用户声纹文件目录（大文件 wav），结构化记录由调用方从库删除。"""
    root = profiles_root().resolve()
    safe = "".join(c for c in (uid or "") if c.isalnum() or c in "-_@.").strip(".")
    if not safe or ".." in safe:
        safe = "unknown"
    target = (root / safe).resolve()
    if target != root and root in target.parents:
        shutil.rmtree(target, ignore_errors=True)
