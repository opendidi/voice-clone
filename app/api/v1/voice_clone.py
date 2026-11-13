# -*- coding: UTF-8 -*-
"""MOSS-TTS-Nano (0.1B) 语音克隆接口。

  GET    /v1/voice-clone/status          引擎探测 / 配置 / 安装指引
  POST   /v1/voice-clone/jobs            提交任务（clone / tts / long_tts）-> 202
  GET    /v1/voice-clone/jobs/<id>       任务状态 / 进度 / 结果
  DELETE /v1/voice-clone/jobs/<id>       取消任务
  POST   /v1/voice-clone/clone           同步克隆（兼容旧客户端）
  GET    /v1/voice-clone/audio/<id>      播放生成的 WAV（?download=1 下载）
  GET    /v1/voice-clone/meta/<id>       生成元数据
  GET    /v1/voice-clone/profiles        语音档案列表
  POST   /v1/voice-clone/profiles        保存语音档案
  DELETE /v1/voice-clone/profiles/<id>   删除语音档案
  POST   /v1/voice-clone/tts             用已存音色同步合成
  POST   /v1/voice-clone/tts/segment     句级合成（实时播报逐句调用）

所有接口统一走 @token_required（单机免登录时 uid=local）与 ProtocolBuilder
envelope（code/data/message/request_id/timestamp）。耗时推理走 /jobs 异步链路，
同步接口仅为兼容保留。响应里的 code 与 HTTP 状态码保持一致。
"""
from __future__ import annotations

import logging
import math
import shutil
import uuid
from typing import Any

from flask import Blueprint, jsonify, request, send_file

from app.config.protocol import StatusCode
from app.config.settings import get_settings
from app.plugin.auth import current_uid, token_required
from app.util import jobs, long_tts, moss_tts, voice_profiles
from app.util.inference_slots import SlotCanceled, SlotsBusy
from app.util.protocol_handler import ProtocolBuilder
from app.util.rate_limit import rate_limit

logger = logging.getLogger(__name__)

voice_clone_api = Blueprint("voice_clone", __name__)

MAX_SEGMENT_CHARS = 120
_SEG_FRAMES_PER_CHAR = 4.6


def _segment_frames(chars: int) -> int:
    return min(1500, max(375, math.ceil(chars * _SEG_FRAMES_PER_CHAR)))


def _ok(data: Any, message: str = "ok"):
    return jsonify(ProtocolBuilder.build_response(data, StatusCode.SUCCESS, message))


def _error(code: StatusCode, message: str, hint: str = "", data: dict | None = None):
    payload = dict(data or {})
    if hint:
        payload["hint"] = hint
    return jsonify(ProtocolBuilder.build_response(payload, code, message)), code.value


def _synth_options(source: Any) -> moss_tts.SynthOptions:
    """从 form / JSON 构造统一的推理选项，越界值一律夹紧。"""

    def _int(name: str, default: int, low: int, high: int) -> int:
        try:
            value = int(source.get(name, default))
        except (TypeError, ValueError):
            return default
        return min(high, max(low, value))

    seed_raw = str(source.get("seed") or "").strip()
    try:
        seed = int(seed_raw) if seed_raw else None
    except ValueError:
        seed = None
    return moss_tts.SynthOptions(
        sample_mode=str(source.get("sample_mode") or "fixed").strip(),
        seed=seed,
        cpu_threads=_int("cpu_threads", 4, 1, 32),
        max_new_frames=_int("max_new_frames", 375, 32, 1500),
        backend_override=str(source.get("backend") or "").strip(),
    )


def _read_text(source: Any, limit: int, truncate: bool) -> tuple[str, bool]:
    text = str(source.get("text") or "").strip()
    truncated = len(text) > limit
    if truncated and truncate:
        text = text[:limit].rstrip()
    return text, truncated


def _save_upload(upload: Any, work: Any) -> tuple[Any, dict]:
    work.mkdir(parents=True, exist_ok=True)
    ref = work / "reference.wav"
    upload.save(str(ref))
    return ref, moss_tts.validate_reference_audio(ref)


def _reference_meta(info: dict) -> dict:
    return {
        "duration": info["duration"],
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "size": info["size"],
    }


def _inference_error(exc: Exception):
    """同步接口的异常 -> envelope。异步任务由 jobs._execute 统一捕获。"""
    if isinstance(exc, SlotsBusy):
        return _error(StatusCode.SERVICE_UNAVAILABLE, str(exc))
    if isinstance(exc, SlotCanceled):
        return _error(StatusCode.BAD_REQUEST, "任务已取消")
    if isinstance(exc, moss_tts.EngineNotReady):
        return _error(StatusCode.SERVICE_UNAVAILABLE, str(exc), hint=exc.hint)
    if isinstance(exc, moss_tts.InferenceError):
        logger.warning("voice-clone inference failed", exc_info=True)
        return _error(StatusCode.INTERNAL_ERROR, str(exc))
    if isinstance(exc, ValueError):
        return _error(StatusCode.BAD_REQUEST, str(exc))
    logger.exception("voice-clone unexpected error")
    return _error(StatusCode.INTERNAL_ERROR, f"服务异常：{exc}")


# --------------------------------------------------------------------------- #
# 引擎状态
# --------------------------------------------------------------------------- #
@voice_clone_api.route("/status", methods=["GET"])
@token_required
@rate_limit(60, 60)
def voice_clone_status():
    """前端启动时探测引擎；刷新按钮强制重探（绕过 TTL 缓存）。"""
    try:
        return _ok(moss_tts.probe(force=True))
    except Exception as exc:
        logger.warning("voice-clone status probe failed", exc_info=True)
        return _error(
            StatusCode.INTERNAL_ERROR,
            f"引擎状态探测失败：{exc}",
            data={"ready": False, "backend": "unavailable"},
        )


# --------------------------------------------------------------------------- #
# 异步任务（推荐入口）
# --------------------------------------------------------------------------- #
@voice_clone_api.route("/jobs", methods=["POST"])
@token_required
@rate_limit(20, 60)
def create_voice_job():
    """提交推理任务。

    multipart（audio + text）-> clone；JSON（text + profile_id）-> tts / long_tts。
    上传文件与参数校验在请求线程里同步完成，失败立即返回 4xx，不占用任务槽位。
    """
    uid = current_uid()
    cfg = get_settings()
    work = None
    submitted = False
    try:
        if "multipart" in (request.content_type or ""):
            text, too_long = _read_text(request.form, moss_tts.MAX_TEXT_CHARS, False)
            if not text:
                return _error(StatusCode.BAD_REQUEST, "请输入要合成的文本")
            if too_long:
                return _error(
                    StatusCode.BAD_REQUEST,
                    f"文本过长（{len(text)} 字），上限 {moss_tts.MAX_TEXT_CHARS} 字",
                )
            upload = request.files.get("audio")
            if upload is None or not upload.filename:
                return _error(StatusCode.BAD_REQUEST, "请上传或录制一段参考音频")
            opts = _synth_options(request.form)
            work = moss_tts.tmp_dir() / f"upload-{uuid.uuid4().hex[:16]}"
            try:
                ref, ref_info = _save_upload(upload, work)
            except ValueError as exc:
                return _error(StatusCode.BAD_REQUEST, str(exc))
            ref_meta = _reference_meta(ref_info)
            kind = "clone"

            def runner(job: jobs.Job) -> dict[str, Any]:
                try:
                    jobs.report_progress(job, "准备就绪", 15)
                    result = moss_tts.synthesize(
                        text, ref, opts, owner=uid,
                        cancel_event=job.cancel_event,
                        progress=lambda stage, percent: jobs.report_progress(job, stage, percent),
                    )
                    result["reference"] = ref_meta
                    return result
                finally:
                    shutil.rmtree(work, ignore_errors=True)

        else:
            data = request.get_json(silent=True) or {}
            long_mode = bool(data.get("long"))
            limit = cfg.long_tts_max_chars if long_mode else moss_tts.MAX_TEXT_CHARS
            text, truncated = _read_text(data, limit, True)
            if not text:
                return _error(StatusCode.BAD_REQUEST, "请输入要合成的文本")
            pid = str(data.get("profile_id") or "").strip()
            if not pid:
                return _error(StatusCode.BAD_REQUEST, "请选择语音档案")
            ref = voice_profiles.get_profile_ref(uid, pid)
            if ref is None:
                return _error(StatusCode.NOT_FOUND, "语音档案不存在或已删除")
            opts = _synth_options(data)
            kind = "long_tts" if long_mode else "tts"

            def runner(job: jobs.Job) -> dict[str, Any]:
                jobs.report_progress(job, "准备就绪", 15)
                if long_mode:
                    result = long_tts.synthesize_long(
                        text, ref, opts=opts, owner=uid,
                        cancel_event=job.cancel_event,
                        report=lambda stage, percent, detail="": jobs.report_progress(
                            job, stage, percent, detail
                        ),
                    )
                else:
                    result = moss_tts.synthesize(
                        text, ref, opts, owner=uid,
                        cancel_event=job.cancel_event,
                        progress=lambda stage, percent: jobs.report_progress(job, stage, percent),
                    )
                result["profile_id"] = pid
                if truncated:
                    result["truncated"] = True
                    result["log"] = (result.get("log", "") + f"；文本已截断到 {limit} 字").strip("；")
                return result

        try:
            job = jobs.create_job(kind, uid, runner)
        except jobs.JobsBusy as exc:
            return _error(StatusCode.SERVICE_UNAVAILABLE, str(exc))
        submitted = True
        logger.info("job %s created: kind=%s owner=%s", job.id, kind, uid)
        return jsonify(
            ProtocolBuilder.build_response(job.to_dict(), StatusCode.SUCCESS, "任务已创建")
        ), 202
    except ValueError as exc:
        return _error(StatusCode.BAD_REQUEST, str(exc))
    finally:
        # 提交失败时清掉刚保存的上传文件；成功时由 runner 的 finally 负责
        if work is not None and not submitted:
            shutil.rmtree(work, ignore_errors=True)


@voice_clone_api.route("/jobs/<job_id>", methods=["GET"])
@token_required
def get_voice_job(job_id: str):
    job = jobs.get_job(job_id, current_uid())
    if job is None:
        return _error(StatusCode.NOT_FOUND, "任务不存在或已过期")
    return _ok(job.to_dict())


@voice_clone_api.route("/jobs/<job_id>", methods=["DELETE"])
@token_required
def cancel_voice_job(job_id: str):
    job = jobs.cancel_job(job_id, current_uid())
    if job is None:
        return _error(StatusCode.NOT_FOUND, "任务不存在或已过期")
    return _ok(job.to_dict(), "任务已取消")


# --------------------------------------------------------------------------- #
# 同步接口（兼容旧客户端）
# --------------------------------------------------------------------------- #
@voice_clone_api.route("/clone", methods=["POST"])
@token_required
@rate_limit(10, 60)
def voice_clone_run():
    text, too_long = _read_text(request.form, moss_tts.MAX_TEXT_CHARS, False)
    if not text:
        return _error(StatusCode.BAD_REQUEST, "请输入要合成的文本")
    if too_long:
        return _error(
            StatusCode.BAD_REQUEST,
            f"文本过长（{len(text)} 字），上限 {moss_tts.MAX_TEXT_CHARS} 字",
        )
    upload = request.files.get("audio")
    if upload is None or not upload.filename:
        return _error(StatusCode.BAD_REQUEST, "请上传或录制一段参考音频")
    opts = _synth_options(request.form)
    work = moss_tts.tmp_dir() / uuid.uuid4().hex[:16]
    try:
        try:
            ref, ref_info = _save_upload(upload, work)
        except ValueError as exc:
            return _error(StatusCode.BAD_REQUEST, str(exc))
        result = moss_tts.synthesize(text, ref, opts, owner=current_uid())
        result["reference"] = _reference_meta(ref_info)
        msg = "演示模式：仅返回提示音，未做真实克隆" if result["demo"] else "语音克隆完成"
        return _ok(result, msg)
    except Exception as exc:
        return _inference_error(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


@voice_clone_api.route("/audio/<file_id>", methods=["GET", "HEAD"])
@token_required
def voice_clone_audio(file_id: str):
    path = moss_tts.get_output_path(file_id, current_uid())
    if path is None:
        return _error(StatusCode.NOT_FOUND, "音频不存在或已过期")
    resp = send_file(
        path,
        mimetype="audio/wav",
        as_attachment=request.args.get("download") == "1",
        download_name=f"voice-clone-{file_id}.wav",
        conditional=True,
    )
    resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


@voice_clone_api.route("/meta/<file_id>", methods=["GET"])
@token_required
def voice_clone_meta(file_id: str):
    meta = moss_tts.get_output_meta(file_id, current_uid())
    if not meta:
        return _error(StatusCode.NOT_FOUND, "记录不存在或已过期")
    return _ok(meta)


# --------------------------------------------------------------------------- #
# 语音档案（按用户隔离）
# --------------------------------------------------------------------------- #
@voice_clone_api.route("/profiles", methods=["GET"])
@token_required
def list_profiles():
    return _ok({"profiles": voice_profiles.list_profiles(current_uid())})


@voice_clone_api.route("/profiles", methods=["POST"])
@token_required
@rate_limit(10, 60)
def save_profile():
    upload = request.files.get("audio")
    if upload is None or not upload.filename:
        return _error(StatusCode.BAD_REQUEST, "请上传一段参考音频")
    name = str(request.form.get("name") or "").strip()
    work = moss_tts.tmp_dir() / uuid.uuid4().hex[:16]
    try:
        try:
            ref, _ = _save_upload(upload, work)
            item = voice_profiles.save_profile(current_uid(), name, ref)
        except ValueError as exc:
            return _error(StatusCode.BAD_REQUEST, str(exc))
        return _ok({"profile": item}, "语音档案已保存")
    except Exception as exc:
        logger.exception("save voice profile failed")
        return _error(StatusCode.INTERNAL_ERROR, f"保存失败：{exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@voice_clone_api.route("/profiles/<pid>", methods=["DELETE"])
@token_required
def delete_profile(pid: str):
    if not voice_profiles.delete_profile(current_uid(), pid):
        return _error(StatusCode.NOT_FOUND, "档案不存在或已删除")
    return _ok({"ok": True}, "语音档案已删除")


# --------------------------------------------------------------------------- #
# 文本合成
# --------------------------------------------------------------------------- #
@voice_clone_api.route("/tts", methods=["POST"])
@token_required
@rate_limit(10, 60)
def voice_clone_tts():
    uid = current_uid()
    data = request.get_json(silent=True) or {}
    text, truncated = _read_text(data, moss_tts.MAX_TEXT_CHARS, True)
    if not text:
        return _error(StatusCode.BAD_REQUEST, "请输入要合成的文本")
    pid = str(data.get("profile_id") or "").strip()
    if not pid:
        return _error(StatusCode.BAD_REQUEST, "请选择语音档案")
    ref = voice_profiles.get_profile_ref(uid, pid)
    if ref is None:
        return _error(StatusCode.NOT_FOUND, "语音档案不存在或已删除")
    opts = _synth_options(data)
    try:
        result = moss_tts.synthesize(text, ref, opts, owner=uid)
        result["profile_id"] = pid
        if truncated:
            result["truncated"] = True
            result["log"] = (result.get("log", "") + "；文本已截断到 1000 字，长文请用 /jobs 的 long 模式").strip("；")
        return _ok(result, "克隆语音合成完成")
    except Exception as exc:
        return _inference_error(exc)


@voice_clone_api.route("/tts/segment", methods=["POST"])
@token_required
@rate_limit(40, 60)
def voice_clone_tts_segment():
    uid = current_uid()
    data = request.get_json(silent=True) or {}
    text, truncated = _read_text(data, MAX_SEGMENT_CHARS, True)
    if not text:
        return _error(StatusCode.BAD_REQUEST, "句子内容为空")
    pid = str(data.get("profile_id") or "").strip()
    if not pid:
        return _error(StatusCode.BAD_REQUEST, "请选择语音档案")
    ref = voice_profiles.get_profile_ref(uid, pid)
    if ref is None:
        return _error(StatusCode.NOT_FOUND, "语音档案不存在或已删除")
    seed_raw = str(data.get("seed") or "").strip()
    try:
        seed = int(seed_raw) if seed_raw else None
    except ValueError:
        seed = None
    opts = moss_tts.SynthOptions(
        sample_mode="fixed", seed=seed, max_new_frames=_segment_frames(len(text))
    )
    try:
        result = moss_tts.synthesize(text, ref, opts, owner=uid)
        result["profile_id"] = pid
        if truncated:
            result["truncated"] = True
        return _ok(result, "句子合成完成")
    except Exception as exc:
        return _inference_error(exc)
