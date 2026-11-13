# -*- coding: UTF-8 -*-
"""SQLite 持久化层：轻量、零运维、文件即库。

只承载结构化元数据，大文件（参考音频 wav、合成产物 wav）仍落在 var/ 目录：
  * profiles 表 —— 替代原先每用户一个的 index.json（声纹元信息）
  * jobs     表 —— 替代原先纯内存的任务表，支持进程重启后断点恢复

设计要点：
  * 每次操作开短连接（sqlite3 连接非线程安全），WAL 模式支持并发读 + 单写；
  * 写操作统一走全局锁 + busy_timeout，避免多线程 / 跨进程写冲突；
  * 大文件不进库，避免把几百 MB 音频塞进 SQLite 撑爆库文件、拖慢查询。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.config.settings import get_settings

_WRITE_LOCK = threading.Lock()


def db_path() -> Path:
    return get_settings().data_dir / "voice-clone.db"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_INITIALIZED_PATHS: set[str] = set()


def _ensure_initialized() -> None:
    """懒初始化：首次访问某 db 文件时若表不存在则建表（按路径缓存，跨库/测试互不干扰）。

    init_db 内部用 CREATE IF NOT EXISTS，并发首次调用幂等安全。
    """
    path = str(db_path())
    if path in _INITIALIZED_PATHS:
        return
    with _WRITE_LOCK:
        if path in _INITIALIZED_PATHS:
            return
        init_db()
        _INITIALIZED_PATHS.add(path)


def init_db() -> None:
    """建表。调用方需保证串行（_ensure_initialized 持 _WRITE_LOCK 时调用，
    或启动时直接调用）。CREATE TABLE IF NOT EXISTS 幂等，并发安全。
    注意：本函数不得再获取 _WRITE_LOCK，否则与 _ensure_initialized 形成自死锁。
    """
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id          TEXT PRIMARY KEY,
                uid         TEXT NOT NULL,
                name        TEXT NOT NULL,
                file        TEXT NOT NULL,
                sample_rate INTEGER NOT NULL,
                channels    INTEGER NOT NULL,
                duration    REAL NOT NULL,
                size        INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_profiles_uid ON profiles(uid);

            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                kind        TEXT NOT NULL,
                owner       TEXT NOT NULL,
                status      TEXT NOT NULL,
                stage       TEXT NOT NULL DEFAULT '排队中',
                percent     INTEGER NOT NULL DEFAULT 0,
                detail      TEXT NOT NULL DEFAULT '',
                error       TEXT NOT NULL DEFAULT '',
                hint        TEXT NOT NULL DEFAULT '',
                result_json TEXT,
                params_json TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            """
        )
        conn.commit()


def _write(sql: str, params: tuple = ()) -> None:
    _ensure_initialized()
    with _WRITE_LOCK, _connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def _exec_count(sql: str, params: tuple = ()) -> int:
    with _WRITE_LOCK, _connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def _query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    _ensure_initialized()
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


# ── profiles ────────────────────────────────────────────────────────────────
def upsert_profile(item: dict) -> None:
    _write(
        """
        INSERT OR REPLACE INTO profiles
            (id, uid, name, file, sample_rate, channels, duration, size, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(item["id"]), str(item["uid"]), str(item["name"]), str(item["file"]),
            int(item["sample_rate"]), int(item["channels"]),
            float(item["duration"]), int(item["size"]),
            int(item["created_at"]),
        ),
    )


def list_profiles(uid: str) -> list[dict]:
    return [dict(r) for r in _query(
        "SELECT * FROM profiles WHERE uid = ? ORDER BY created_at DESC", (uid,)
    )]


def get_profile(uid: str, pid: str) -> dict | None:
    rows = _query("SELECT * FROM profiles WHERE uid = ? AND id = ?", (uid, pid))
    return dict(rows[0]) if rows else None


def delete_profile(uid: str, pid: str) -> bool:
    return _exec_count(
        "DELETE FROM profiles WHERE uid = ? AND id = ?", (uid, pid)
    ) > 0


def delete_profiles_by_owner(uid: str) -> int:
    return _exec_count("DELETE FROM profiles WHERE uid = ?", (uid,))


# ── jobs ────────────────────────────────────────────────────────────────────
def insert_job(job: dict) -> None:
    params = job.get("params")
    params_json = json.dumps(params, ensure_ascii=False) if params is not None else None
    result = job.get("result")
    result_json = json.dumps(result, ensure_ascii=False) if result else None
    _write(
        """
        INSERT OR REPLACE INTO jobs
            (id, kind, owner, status, stage, percent, detail, error, hint,
             result_json, params_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(job["id"]), str(job["kind"]), str(job["owner"]), str(job["status"]),
            str(job["stage"]), int(job["percent"]), str(job["detail"]),
            str(job["error"]), str(job["hint"]), result_json, params_json,
            int(job["created_at"]), int(job["updated_at"]),
        ),
    )


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    values: list[Any] = []
    cols: list[str] = []
    for key, value in fields.items():
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        cols.append(f"{key} = ?")
        values.append(value)
    values.append(job_id)
    _write(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", tuple(values))


def get_job(job_id: str) -> dict | None:
    rows = _query("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return dict(rows[0]) if rows else None


def get_active_jobs() -> list[dict]:
    return [dict(r) for r in _query(
        "SELECT * FROM jobs WHERE status IN ('queued', 'running')"
    )]


def cleanup_jobs(ttl_seconds: int, now: float) -> int:
    return _exec_count(
        "DELETE FROM jobs WHERE status IN ('succeeded','failed','canceled') "
        "AND ? - updated_at > ?",
        (now, ttl_seconds),
    )


def delete_jobs_by_owner(uid: str) -> int:
    return _exec_count("DELETE FROM jobs WHERE owner = ?", (uid,))


def reset_jobs_table() -> None:
    _write("DELETE FROM jobs")


# ── 迁移：把老版本 index.json 声纹索引搬进 SQLite ──────────────────────────
def migrate_legacy_profiles() -> int:
    profiles_dir = get_settings().profiles_dir
    if not profiles_dir.is_dir():
        return 0
    migrated = 0
    for index in profiles_dir.rglob("index.json"):
        parent = index.parent
        if parent == profiles_dir:
            continue  # 不该出现在根目录，跳过避免污染 uid
        uid = parent.name
        try:
            items = json.loads(index.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("id") or "")
            if len(pid) != 12 or not all(c in "0123456789abcdef" for c in pid):
                continue
            upsert_profile({
                "id": pid,
                "uid": uid,
                "name": str(it.get("name") or "音色"),
                "file": str(it.get("file") or f"{pid}.wav"),
                "sample_rate": int(it.get("sample_rate") or 0),
                "channels": int(it.get("channels") or 1),
                "duration": float(it.get("duration") or 0.0),
                "size": int(it.get("size") or 0),
                "created_at": int(it.get("created_at") or 0),
            })
            migrated += 1
        # 迁移成功后删除 json，避免下次启动重复导入、双写不一致
        try:
            index.unlink()
        except OSError:
            pass
    return migrated


# ── 重置：清空某用户的结构化数据（大文件由调用方物理删除）─────────────────
def reset_owner_data(uid: str) -> dict[str, int]:
    return {
        "profiles": delete_profiles_by_owner(uid),
        "jobs": delete_jobs_by_owner(uid),
    }
