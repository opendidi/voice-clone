# -*- coding: UTF-8 -*-
"""跨进程 / 跨线程的推理并发闸门。

ONNX 权重约 728MB，同时跑多路推理会把内存和 CPU 打满。进程内的
threading.Semaphore 在多 worker（gunicorn / waitress 多进程）部署下会失效，
所以这里用 O_CREAT|O_EXCL 锁文件实现「最多 N 路」的信号量：
每个 slot 一个锁文件，持有者写入唯一 token，释放时校验 token 再删除。

对崩溃残留的锁用租约（lease）判定过期；清理时先原子 rename 再重新 O_EXCL，
避免两个进程同时删/建同一个 stale 锁而各自以为自己拿到了槽位。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config.settings import get_settings

logger_name = "voice-clone.slots"

import logging  # noqa: E402

logger = logging.getLogger(logger_name)


class SlotsBusy(RuntimeError):
    """等待超时，没有空闲推理槽位。调用方应映射为 503。"""


class SlotCanceled(RuntimeError):
    """等待槽位期间任务被取消。"""


def _is_stale(path: Path, lease: int) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created = float(payload.get("ts", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        # 读不出来 / 写了一半的锁文件，视为失效
        return True
    return time.time() - created > lease


def _try_acquire(path: Path, payload: dict, lease: int) -> tuple[bool, str | None]:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if not _is_stale(path, lease):
            return False, None
        # 原子挪走 stale 锁：谁 rename 成功谁才有资格重新创建
        try:
            os.rename(path, f"{path}.stale-{uuid.uuid4().hex[:8]}")
        except OSError:
            return False, None
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False, None
    token = payload["token"]
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
    return True, token


def _release(path: Path, token: str) -> None:
    """只删除仍由自己持有的锁，避免误删被其他进程重建的锁。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("token") != token:
            return
        path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def cleanup_stale_files() -> int:
    """启动时清掉历史残留的 .stale-* 文件，返回清理数量。"""
    cfg = get_settings()
    removed = 0
    try:
        cfg.lock_dir.mkdir(parents=True, exist_ok=True)
        for path in cfg.lock_dir.glob("*.stale-*"):
            try:
                if time.time() - path.stat().st_mtime > 60:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


@contextmanager
def inference_slot(
    *, wait_seconds: int | None = None, cancel_event: threading.Event | None = None
) -> Iterator[None]:
    """占用一个推理槽位；排队超时抛 SlotsBusy，取消时抛 SlotCanceled。"""
    cfg = get_settings()
    cfg.lock_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (wait_seconds if wait_seconds is not None else cfg.slot_wait_seconds)
    payload = {"token": uuid.uuid4().hex, "pid": os.getpid(), "ts": time.time()}

    held: Path | None = None
    token = ""
    while held is None:
        for index in range(cfg.max_inference_slots):
            path = cfg.lock_dir / f"slot-{index}.lock"
            ok, acquired_token = _try_acquire(path, payload, cfg.slot_lease_seconds)
            if ok:
                held, token = path, acquired_token or ""
                break
        if held is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            raise SlotCanceled("任务在排队时已取消")
        if time.monotonic() >= deadline:
            raise SlotsBusy(
                f"推理队列已满（最多 {cfg.max_inference_slots} 路并发），请稍后重试"
            )
        time.sleep(0.2)

    try:
        yield
    finally:
        _release(held, token)
