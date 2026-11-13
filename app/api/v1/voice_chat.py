# -*- coding: UTF-8 -*-
"""语音聊天接口（MVP）：录音 -> ASR -> LLM -> TTS 一回合。

  POST   /v1/voice-chat/turn       提交一回合语音聊天（音频 + 可选 profile_id）-> 202
  GET    /v1/voice-chat/turn/<id>  任务状态 / 进度 / 结果
  DELETE /v1/voice-chat/turn/<id>  取消
  GET    /v1/voice-chat/status     能力探测（ASR / LLM / TTS 后端）

链路异步跑在 jobs 上，前端轮询 turn/<id> 即可拿到阶段进度与最终结果。
"""
from __future__ import annotations

import logging
import shutil
import uuid
import json
from typing import Any

from flask import Blueprint, jsonify, request

from app.config.protocol import StatusCode
from app.plugin.auth import current_uid, token_required
from app.util import asr, jobs, llm, moss_tts, voice_chat
from app.util.protocol_handler import ProtocolBuilder
from app.util.rate_limit import rate_limit

logger = logging.getLogger(__name__)

voice_chat_api = Blueprint("voice_chat", __name__)


def _ok(data: Any, message: str = "ok"):
    return jsonify(ProtocolBuilder.build_response(data, StatusCode.SUCCESS, message))


def _error(code: StatusCode, message: str, hint: str = "", data: dict | None = None):
    payload = dict(data or {})
    if hint:
        payload["hint"] = hint
    return jsonify(ProtocolBuilder.build_response(payload, code, message)), code.value


@voice_chat_api.route("/status", methods=["GET"])
@token_required
@rate_limit(30, 60)
def voice_chat_status():
    try:
        asr_info = asr.probe()
        llm_info = llm.probe()
        tts_info = moss_tts.probe(force=True)
        return _ok({
            "asr": asr_info,
            "llm": llm_info,
            "tts": {
                "backend": tts_info.get("backend"),
                "ready": tts_info.get("ready", False),
                "mode": tts_info.get("mode"),
                "hint": tts_info.get("hint", ""),
            },
        })
    except Exception as exc:
        logger.warning("voice-chat status failed", exc_info=True)
        return _error(StatusCode.INTERNAL_ERROR, f"能力探测失败：{exc}")


@voice_chat_api.route("/turn", methods=["POST"])
@token_required
@rate_limit(10, 60)
def create_turn():
    upload = request.files.get("audio")
    if upload is None or not upload.filename:
        return _error(StatusCode.BAD_REQUEST, "请上传一段录音")
    profile_id = str(request.form.get("profile_id") or "").strip() or None

    # 解析多轮历史（前端把前几轮对话以 JSON 串传入），非法则忽略
    history: list[dict[str, str]] | None = None
    raw_history = request.form.get("history")
    if raw_history:
        try:
            parsed = json.loads(raw_history)
            if isinstance(parsed, list):
                history = [
                    {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
                    for item in parsed
                    if isinstance(item, dict) and item.get("content")
                ] or None
        except (ValueError, TypeError):
            history = None

    uid = current_uid()
    work = moss_tts.tmp_dir() / f"vc-{uuid.uuid4().hex[:16]}"
    work.mkdir(parents=True, exist_ok=True)
    audio = work / "input.wav"
    try:
        upload.save(str(audio))
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        return _error(StatusCode.BAD_REQUEST, f"音频保存失败：{exc}")
    if not audio.is_file() or audio.stat().st_size < 44:
        shutil.rmtree(work, ignore_errors=True)
        return _error(StatusCode.BAD_REQUEST, "录音文件无效或为空")

    def runner(job: jobs.Job) -> dict[str, Any]:
        try:
            return voice_chat.run_pipeline(
                audio, uid, profile_id,
                report=lambda stage, percent, detail="": jobs.report_progress(
                    job, stage, percent, detail
                ),
                history=history,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    try:
        job = jobs.create_job("voice_chat", uid, runner)
    except jobs.JobsBusy as exc:
        return _error(StatusCode.SERVICE_UNAVAILABLE, str(exc))
    return jsonify(
        ProtocolBuilder.build_response(job.to_dict(), StatusCode.SUCCESS, "已提交语音聊天")
    ), 202


@voice_chat_api.route("/turn/<job_id>", methods=["GET"])
@token_required
def get_turn(job_id: str):
    job = jobs.get_job(job_id, current_uid())
    if job is None:
        return _error(StatusCode.NOT_FOUND, "任务不存在或已过期")
    return _ok(job.to_dict())


@voice_chat_api.route("/turn/<job_id>", methods=["DELETE"])
@token_required
def cancel_turn(job_id: str):
    job = jobs.cancel_job(job_id, current_uid())
    if job is None:
        return _error(StatusCode.NOT_FOUND, "任务不存在或已过期")
    return _ok(job.to_dict(), "任务已取消")
