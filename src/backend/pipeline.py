"""课堂处理流水线（协调转写与分析）。

流程：录音导入 -> 检查文件 -> 本地语音识别 -> 生成带时间戳转写 -> 本地文字模型分析 ->
保存课堂概览 -> 保存重要事项 -> 建立搜索索引 -> 完成。

约束：
- 同一时间默认只处理一条长录音（由 API 层用 active_jobs 强制）。
- 支持取消（jobs.is_cancelled）、失败后保留错误原因、应用重启后由 mark_interrupted_jobs 标记中断任务。
- 转写结果分批已在 whisper_worker 内顺序产生，这里整段写入数据库（课堂录音通常可控；如需超长可改流式）。
"""
from __future__ import annotations

import json
import os

from . import db, jobs, hashutil
from .logutil import get_logger
from .ai import llm_provider, manifest as aimanifest, segment_analysis, whisper_worker
from .ai import verification
from .ai.provider import build_provider

LOG = get_logger("pipeline")


def _resolve_whisper_size(tier_or_size, settings: dict) -> str:
    # 旧数据库可能仍保存 fast/balanced/accurate；升级后无条件使用唯一的 Qwen ASR。
    return aimanifest.WHISPER_TIERS[aimanifest.DEFAULT_WHISPER_TIER]["model_size"]


def _report_transcribe(job_id, session_id, sec, duration=None):
    if jobs.is_cancelled(session_id):
        raise InterruptedError("已取消")
    elapsed = f"{int(sec) // 60}分{int(sec) % 60:02d}秒"
    if duration and duration > 0:
        total = f"{int(duration) // 60}分{int(duration) % 60:02d}秒"
        percent = min(100, int(sec / duration * 100))
        stage = f"语音识别中（{elapsed} / {total}，{percent}%）"
    else:
        stage = f"语音识别中（已处理 {elapsed}）"
    db.update_job(job_id, stage=stage)


def _transcribe_audio_files(session, course, settings, whisper_size, language, job_id):
    """Transcribe all recordings in order and shift later timestamps forward."""
    files = db.audio_files_for_session(session)
    if not files:
        raise FileNotFoundError("找不到原始录音文件，可能已被移动或删除")
    for entry in files:
        if not entry.get("path") or not os.path.exists(entry["path"]):
            raise FileNotFoundError(f"找不到录音文件：{entry.get('name') or entry.get('path') or '未知文件'}")

    durations = []
    for entry in files:
        duration = hashutil.audio_duration_seconds(entry["path"])
        if duration is None and entry.get("duration") is not None:
            try:
                duration = float(entry["duration"])
            except (TypeError, ValueError):
                duration = None
        durations.append(duration)
    total_duration = sum(durations) if all(d is not None for d in durations) else None
    if total_duration is not None:
        db.update_session(session["id"], audio_duration_seconds=total_duration)
        session["audio_duration_seconds"] = total_duration

    device_pref = (settings.get("whisper_device") or "auto").lower()
    requested_language = language or course.get("language_mode", "auto")
    all_segments = []
    detected_languages = []
    offset = 0.0
    used_device = "cpu"
    for index, entry in enumerate(files):
        if jobs.is_cancelled(session["id"]):
            raise InterruptedError("已取消")
        file_duration = durations[index]
        db.update_job(job_id, stage=f"正在识别第 {index + 1}/{len(files)} 段录音")
        try:
            plan = whisper_worker.resolve_device(device_pref)
            db.update_job(job_id, stage=(
                f"正在读取第 {index + 1}/{len(files)} 段录音并加载本地模型（"
                f"{'显卡加速' if plan['used_cuda'] else 'CPU 识别，大录音需要一些时间'}）"
            ))
        except Exception:  # noqa: BLE001
            pass
        segments, detected, used_device = whisper_worker.transcribe(
            entry["path"], size=whisper_size, language=requested_language,
            device_pref=device_pref,
            vocabulary=course.get("vocabulary", ""),
            progress_cb=lambda sec, base=offset: _report_transcribe(
                job_id, session["id"], base + sec, total_duration),
            on_retry=lambda reason, msg: db.update_job(
                job_id, stage="GPU 不可用，已自动切到 CPU 重新识别"),
        )
        detected_languages.append(detected)
        for segment in segments:
            shifted = dict(segment)
            if shifted.get("start_seconds") is not None:
                shifted["start_seconds"] = round(float(shifted["start_seconds"]) + offset, 2)
            if shifted.get("end_seconds") is not None:
                shifted["end_seconds"] = round(float(shifted["end_seconds"]) + offset, 2)
            all_segments.append(shifted)
        max_end = max((float(s.get("end_seconds") or 0) for s in segments), default=0.0)
        offset += file_duration if file_duration is not None else max_end

    if not all_segments:
        raise ValueError("这些录音中没有识别到清晰的人声。请先播放录音确认有声音，再尝试重新转写。")
    if requested_language == "mix" or len(set(detected_languages)) > 1:
        detected_language = "mix"
    else:
        detected_language = detected_languages[0] if detected_languages else None
    return all_segments, detected_language, used_device


def run_pipeline(session_id, mode="transcribe", whisper_tier=None, language=None,
                 settings=None, job_id=None) -> dict:
    session = db.get_session(session_id)
    if not session:
        raise ValueError("课堂记录不存在")
    course = db.get_course(session["course_id"])
    if not course:
        raise ValueError("课程不存在")
    settings = settings or db.get_all_settings()
    whisper_size = _resolve_whisper_size(whisper_tier, settings)
    llm_model_id = settings.get("llm_model_id") or aimanifest.default_llm_model()["id"]
    llm_provider.set_cancel_check(lambda: jobs.is_cancelled(session_id))

    try:
        db.snapshot_session(session_id, "重新转写前" if mode in ("transcribe", "retranscribe") else "重新分析前")
        if mode in ("transcribe", "retranscribe"):
            db.update_session(session_id, status="file_check", processing_error=None)
            db.update_job(job_id, status="file_check", stage="检查音频文件")
            db.update_session(session_id, status="transcribing")
            db.update_job(job_id, status="transcribing",
                          stage="正在加载本地语音模型并读取录音，请稍候")
            segments, lang, device = _transcribe_audio_files(
                session, course, settings, whisper_size, language, job_id)
            db.replace_segments(session_id, segments)
            db.update_session(session_id, transcript_language=lang,
                              transcription_model=f"Qwen3-ASR-0.6B/{device}", status="analyzing")
            # Do not display notes/evidence from the previous transcript as current.
            db.replace_topics(session_id, [])
            db.replace_items(session_id, [])
            db.update_session(session_id, notes_data=None)
            db.index_session(session_id)
        else:
            segments = db.list_segments(session_id)
            if not segments:
                raise ValueError("没有可分析的转写内容（请先转写或导入文字）")
            if session.get("source_type") == "audio" and session.get("transcript_language") not in {"zh", "en", "mix", None}:
                raise ValueError("旧转写的语言不符合中英文课堂，请先选择正确授课语言并「重新转写」。仅重新分析不能修复识别错误。")

        db.update_session(session_id, status="analyzing", processing_error=None)
        db.update_job(job_id, status="analyzing", stage="课堂分析中")
        provider = build_provider(settings)
        if isinstance(provider, llm_provider.LlamaCppSidecarLLM):
            provider.start()
        try:
            meta = {
                "session_date": session["session_date"],
                "course_name": course["name"],
                "language_mode": course.get("language_mode", "auto"),
                "transcript_language": db.get_session(session_id).get("transcript_language"),
            }
            display_segments = segment_analysis.translate_for_display(
                provider, segments, meta,
                progress_cb=lambda st, p: db.update_job(job_id, stage=f"{st} {int(p * 100)}%"),
            )
            db.update_segment_display_texts(session_id, display_segments)
            overview, items = segment_analysis.analyze_session(
                provider, display_segments, meta,
                progress_cb=lambda st, p: db.update_job(job_id, stage=f"{st if st != 'analyzing' else '整理章节'} {int(p * 100)}%"),
            )
        finally:
            if isinstance(provider, llm_provider.LlamaCppSidecarLLM):
                provider.stop()

        db.update_session(session_id, status="indexing")
        if jobs.is_cancelled(session_id):
            raise InterruptedError('已取消')
        db.update_job(job_id, status="indexing", stage="保存结果并建立搜索索引")

        # ---- 第五阶段：5 层证据核查关卡（模型输出之后的独立验证）----
        # 即使模型自信地返回一条通知，也要逐条独立核查：结构 / 原文存在性 / 上下文 / 语言信号 / 展示规则。
        # confirmed + pending 进入正式列表；rejected 仍入库（便于调试）但默认隐藏。
        audio_duration = session.get("audio_duration_seconds")
        vres = verification.apply_verification(
            items, display_segments, {"audio_duration_seconds": audio_duration}
        )
        LOG.info(
            "证据核查 session=%s confirmed=%d pending=%d rejected=%d",
            session_id, len(vres["confirmed"]), len(vres["pending"]), len(vres["rejected"]),
        )

        items_db = [
            {**it, "session_id": session_id, "course_id": session["course_id"]}
            for it in (vres["confirmed"] + vres["pending"] + vres["rejected"])
        ]
        # The visible non-knowledge section uses the same verified items as the
        # task list.  Keep the Chinese teacher wording/details and real source
        # evidence together so the reader can jump back to the audio.
        notes = dict(overview["notes"] or {})
        notes["non_knowledge"] = [
            {k: it.get(k) for k in (
                "category", "title", "details", "raw_date_text", "normalized_date",
                "action_required", "confidence", "verification_status", "evidence",
            )}
            for it in (vres["confirmed"] + vres["pending"])
        ]
        if notes.get("title") and (not session.get("title") or session.get("title") == course.get("name")):
            db.update_session(session_id, title=notes["title"])
        db.save_analysis(session_id, overview["topics"], items_db, notes, provider.model_label())
        db.update_job(job_id, status="completed", finished_at=db.now_iso(),
                      progress_data=json.dumps({"topics": len(overview["topics"]), "items": len(items)}))
        jobs.clear_cancel(session_id)
        LOG.info("处理完成 session=%s topics=%d items=%d", session_id, len(overview["topics"]), len(items))
        return {"status": "completed", "topics": len(overview["topics"]), "items": len(items)}

    except InterruptedError:
        db.update_job(job_id, status="cancelled", finished_at=db.now_iso(), stage="已取消")
        db.update_session(session_id, status="cancelled", processing_error="用户已取消")
        jobs.clear_cancel(session_id)
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        LOG.exception("处理失败 session=%s", session_id)
        db.update_job(job_id, status="failed", finished_at=db.now_iso(),
                     error_message=msg, stage="处理失败")
        db.update_session(session_id, status="failed", processing_error=msg)
        jobs.clear_cancel(session_id)
        return {"status": "failed", "error": msg}
