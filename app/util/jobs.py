# -*- coding: UTF-8 -*-
"""任务队列：把秒级~分钟级的推理从同步 HTTP 请求里剥离。

  POST   /jobs       -> 202 + job_id
  GET    /jobs/<id>  -> 状态 / 进度 / 结果
  DELETE /jobs/<id>  -> 取消（协作式，运行中任务跑完后丢弃结果）

任务状态同时存在于：
  * 内存热表 _JOBS —— 运行期低延迟读写、进度回调高频更新；
  * SQLite 的 jobs 表（app/util/db.py）—— 进程重启后断点恢复 tts / long_tts。
clone 任务的输入是上传的临时音频，进程崩溃后已丢失，无法恢复，重启时标记为失败。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config.settings import get_settings
from app.util import db

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELED = "canceled"
_TERMINAL = (SUCCEEDED, FAILED, CANCELED)

# 进程启动后由 voice_clone 注册：把持久化的 params 重建为可执行的 runner。
# 这样 jobs 不直接依赖 voice_clone，避免循环导入。
_RUNNER_FACTORY: Callable[[dict], Callable[["Job"], dict[str, Any]]] | None = None


def register_runner_factory(fn: Callable[[dict], Callable[["Job"], dict[str, Any]]]) -> None:
    global _RUNNER_FACTORY
    _RUNNER_FACTORY = fn


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


def _persist(job: Job, params: dict | None = None) -> None:
    """把任务落库（失败不影响内存执行，降级为纯内存）。"""
    try:
        db.insert_job({
            "id": job.id,
            "kind": job.kind,
            "owner": job.owner,
            "status": job.status,
            "stage": job.stage,
            "percent": job.percent,
            "detail": job.detail,
            "error": job.error,
            "hint": job.hint,
            "result": job.result,
            "params": params,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        })
    except Exception:
        logger.warning("任务落库失败：%s", job.id, exc_info=True)


def _persist_update(job: Job) -> None:
    try:
        db.update_job(
            job.id,
            status=job.status, stage=job.stage, percent=job.percent,
            detail=job.detail, error=job.error, hint=job.hint,
            result_json=job.result,
        )
    except Exception:
        logger.warning("任务状态更新落库失败：%s", job.id, exc_info=True)


def _update(job: Job, **changes: Any) -> None:
    with _LOCK:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = time.time()
    _persist_update(job)


def report_progress(job: Job, stage: str, percent: int, detail: str = "") -> None:
    """供推理链路回调；取消后不再更新展示进度。"""
    if job.cancel_event.is_set():
        return
    _update(job, stage=stage, percent=max(0, min(100, int(percent))), detail=detail)


def cleanup_expired() -> int:
    """回收过期任务，返回内存中清理数量（库里的过期记录一并清理）。"""
    ttl = get_settings().job_ttl_seconds
    now = time.time()
    with _LOCK:
        expired = [
            key for key, job in _JOBS.items()
            if job.status in _TERMINAL and now - job.updated_at > ttl
        ]
        for key in expired:
            _JOBS.pop(key, None)
    try:
        db.cleanup_jobs(ttl, now)
    except Exception:
        logger.warning("清理过期任务落库失败", exc_info=True)
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


def create_job(
    kind: str,
    owner: str,
    runner: Callable[[Job], dict[str, Any]],
    params: dict | None = None,
) -> Job:
    """创建任务并落库；params 为可序列化的重建参数，供重启后断点恢复。"""
    cfg = get_settings()
    with _LOCK:
        cleanup_expired()
        active = sum(1 for j in _JOBS.values() if j.status not in _TERMINAL)
        max_active = max(cfg.max_inference_slots * 3, 6)
        if active >= max_active or len(_JOBS) >= cfg.job_max:
            raise JobsBusy("排队任务过多，请等待现有任务结束再提交")
        job = Job(id=uuid.uuid4().hex[:16], kind=kind, owner=owner, runner=runner)
        _JOBS[job.id] = job
    _persist(job, params)
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


def recover_jobs() -> int:
    """进程启动时调用：重放未完成任务。

    tts / long_tts 的输入来自已存声纹（进程重启后仍在），可重建 runner 重新执行；
    clone 的上传音频已随临时目录清理而丢失，无法恢复，标记为失败。
    返回成功恢复（重新入队）的任务数。
    """
    try:
        rows = db.get_active_jobs()
    except Exception:
        logger.warning("读取未完成任务失败，跳过恢复", exc_info=True)
        return 0
    recovered = 0
    for row in rows:
        kind = row["kind"]
        params_json = row["params_json"]
        params = json.loads(params_json) if params_json else None
        if _RUNNER_FACTORY is None or kind not in ("tts", "long_tts") or not params:
            try:
                db.update_job(
                    row["id"], status=FAILED, stage="进程重启后已失效",
                    error="服务重启，该任务的输入或运行环境已丢失",
                )
            except Exception:
                pass
            continue
        try:
            runner = _RUNNER_FACTORY(params)
        except Exception as exc:
            try:
                db.update_job(row["id"], status=FAILED, stage="恢复失败", error=str(exc))
            except Exception:
                pass
            continue
        job = Job(
            id=row["id"], kind=kind, owner=row["owner"], runner=runner,
            status=QUEUED, stage="重启后恢复", percent=0,
        )
        with _LOCK:
            _JOBS[job.id] = job
        threading.Thread(
            target=_execute, args=(job,), daemon=True, name=f"vc-rec-{job.id[:8]}"
        ).start()
        recovered += 1
    if recovered:
        logger.info("已恢复 %d 个未完成的推理任务", recovered)
    return recovered


def reset_jobs() -> None:
    """测试用：清空任务表（内存 + 库）。"""
    with _LOCK:
        _JOBS.clear()
    try:
        db.reset_jobs_table()
    except Exception:
        logger.warning("清空任务库失败", exc_info=True)


def reset_jobs_for_owner(uid: str) -> None:
    """清空某用户的内存任务 + 库中任务（供一键清空数据使用）。"""
    with _LOCK:
        for jid in [k for k, v in _JOBS.items() if v.owner == uid]:
            _JOBS.pop(jid, None)
    try:
        db.delete_jobs_by_owner(uid)
    except Exception:
        logger.warning("清空用户任务库失败：%s", uid, exc_info=True)


class JobsBusy(RuntimeError):
    """排队任务过多，拒绝新建。"""
