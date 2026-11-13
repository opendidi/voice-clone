# -*- coding: UTF-8 -*-
"""内存任务队列：把秒级~分钟级的推理从同步 HTTP 请求里剥离。

  POST   /jobs       -> 202 + job_id
  GET    /jobs/<id>  -> 状态 / 进度 / 结果
  DELETE /jobs/<id>  -> 取消（协作式，运行中任务跑完后丢弃结果）

任务状态只存在当前进程内存里，重启即失效；需要跨实例共享时替换为
Redis / 数据库 + 独立 worker，接口契约不用变。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELED = "canceled"
_TERMINAL = (SUCCEEDED, FAILED, CANCELED)


class JobsBusy(RuntimeError):
    """排队任务过多，拒绝新建。"""


@dataclass
class Job:
    id: str
    kind: str
    owner: str
    runner: Callable[["Job"], dict[str, Any]] = field(repr=False)
    status: str = QUEUED
    stage: str = "排队中"
    percent: int = 0
    detail: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    hint: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "detail": self.detail,
            "result": self.result,
            "error": self.error,
            "hint": self.hint,
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "elapsed_ms": int((time.time() - self.created_at) * 1000),
        }


_JOBS: dict[str, Job] = {}
_LOCK = threading.RLock()


def _update(job: Job, **changes: Any) -> None:
    with _LOCK:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()


def report_progress(job: Job, stage: str, percent: int, detail: str = "") -> None:
    """供推理链路回调；取消后不再更新展示进度。"""
    if job.cancel_event.is_set():
        return
    _update(job, stage=stage, percent=max(0, min(100, int(percent))), detail=detail)


def cleanup_expired() -> int:
    """回收过期任务，返回清理数量。"""
    ttl = get_settings().job_ttl_seconds
    now = time.time()
    with _LOCK:
        expired = [
            key for key, job in _JOBS.items()
            if job.status in _TERMINAL and now - job.updated_at > ttl
        ]
        for key in expired:
            _JOBS.pop(key, None)
    return len(expired)


def _discard_result(result: dict[str, Any] | None) -> None:
    """任务被取消后删掉已生成的音频，避免留下无人认领的产物。"""
    file_id = (result or {}).get("id")
    if not file_id:
        return
    try:
        from app.util import moss_tts

        moss_tts.remove_output(str(file_id))
    except Exception:
        logger.warning("清理已取消任务的产物失败：%s", file_id, exc_info=True)


def _execute(job: Job) -> None:
    _update(job, status=RUNNING, stage="启动", percent=10)
    try:
        result = job.runner(job)
    except Exception as exc:
        logger.exception("job %s failed", job.id)
        from app.util.inference_slots import SlotCanceled

        if isinstance(exc, SlotCanceled) or job.cancel_event.is_set():
            _update(job, status=CANCELED, stage="已取消", detail=str(exc))
        else:
            _update(
                job,
                status=FAILED,
                stage="失败",
                error=str(exc),
                hint=getattr(exc, "hint", "") or "",
            )
    else:
        if job.cancel_event.is_set():
            _discard_result(result)
            _update(job, status=CANCELED, stage="已取消", detail="结果已丢弃")
        else:
            _update(job, status=SUCCEEDED, stage="完成", percent=100, result=result)


def create_job(kind: str, owner: str, runner: Callable[[Job], dict[str, Any]]) -> Job:
    cfg = get_settings()
    with _LOCK:
        cleanup_expired()
        active = sum(1 for j in _JOBS.values() if j.status not in _TERMINAL)
        max_active = max(cfg.max_inference_slots * 3, 6)
        if active >= max_active or len(_JOBS) >= cfg.job_max:
            raise JobsBusy("排队任务过多，请等待现有任务结束再提交")
        job = Job(id=uuid.uuid4().hex[:16], kind=kind, owner=owner, runner=runner)
        _JOBS[job.id] = job
    threading.Thread(
        target=_execute, args=(job,), daemon=True, name=f"vc-job-{job.id[:8]}"
    ).start()
    return job


def get_job(job_id: str, owner: str | None = None) -> Job | None:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None or (owner is not None and job.owner != owner):
        return None
    return job


def cancel_job(job_id: str, owner: str | None = None) -> Job | None:
    job = get_job(job_id, owner)
    if job is None:
        return None
    job.cancel_event.set()
    if job.status not in _TERMINAL:
        _update(job, status=CANCELED, stage="已取消")
    return job


def reset_jobs() -> None:
    """测试用：清空任务表。"""
    with _LOCK:
        _JOBS.clear()
