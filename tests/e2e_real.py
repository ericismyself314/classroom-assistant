"""真实录音端到端验收（非 mock）。

用法：
  python tests/e2e_real.py <一段有清晰人声的音频>

实际经过：音频文件 -> faster-whisper -> llama.cpp/Qwen -> 五层证据核查
          -> SQLite -> 结果回读。数据库使用独立临时目录，模型沿用当前用户目录。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
REAL_MODELS = os.path.join(REAL_LOCALAPPDATA, "ClassroomAssistant", "models")


def main() -> int:
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        print("用法: python tests/e2e_real.py <音频文件路径>")
        return 2
    audio = os.path.abspath(sys.argv[1])
    tmp = tempfile.mkdtemp(prefix="ca_real_e2e_")
    try:
        os.environ["LOCALAPPDATA"] = os.path.join(tmp, "Local")
        os.environ["APPDATA"] = os.path.join(tmp, "Roaming")

        from backend import config, db, hashutil, pipeline
        from backend.ai import llm_provider, manifest, whisper_worker

        cfg = config.init_config()
        # 测试数据隔离，但只读复用用户已下载的模型。
        cfg.dirs.speech_models = os.path.join(REAL_MODELS, "speech")
        cfg.dirs.llm_models = os.path.join(REAL_MODELS, "llm")
        cfg.dirs.llm_bin = os.path.join(REAL_MODELS, "llm", "bin")
        db.init_db()

        whisper_size = manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"]
        model_id = manifest.default_llm_model()["id"]
        sidecar = llm_provider.LlamaCppSidecarLLM(model_id)
        if not whisper_worker.is_model_downloaded(whisper_size):
            print("未找到完整语音模型，请先在应用中点击“一键准备推荐模型”。")
            return 10
        if not sidecar.available():
            print("未找到完整文字模型或 llama.cpp，请先在应用中点击“一键准备推荐模型”。")
            return 10

        sem = db.create_semester("真实端到端验收", "2026-01-01", "2027-12-31", "Asia/Shanghai", is_active=1)
        course = db.create_course(sem, "验收课程", language_mode="auto")
        session = db.get_or_create_session(course, "2026-09-03", title="真实录音验收", status="waiting_model")
        db.update_session(
            session,
            source_type="audio",
            audio_path=audio,
            audio_hash=hashutil.sha256_file(audio),
            audio_duration_seconds=hashutil.audio_duration_seconds(audio),
        )
        db.set_setting("ai_provider", "sidecar")
        db.set_setting("llm_model_id", model_id)
        db.set_setting("whisper_tier", manifest.DEFAULT_WHISPER_TIER)
        job = db.create_job(session, "transcribe", status="pending", stage="真实端到端验收")

        result = pipeline.run_pipeline(session, mode="transcribe", settings=db.get_all_settings(), job_id=job)
        detail = {
            "segments": db.list_segments(session),
            "topics": db.list_topics(session),
            "items": [x for x in db.list_items({"course_id": course}) if x["session_id"] == session],
        }
        assert result.get("status") == "completed", result
        assert detail["segments"], "whisper 未产出转写片段"
        assert detail["topics"], "LLM 未产出课堂概览"
        assert all(x.get("evidence_data") for x in detail["items"]), "重要事项缺少原文证据"
        print(f"PASS: {len(detail['segments'])} 段转写，{len(detail['topics'])} 个主题，{len(detail['items'])} 条重要事项")
        return 0
    finally:
        try:
            from backend import db
            db.close_all_connections()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
