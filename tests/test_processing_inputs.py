"""回归测试：文字导入、PyAV 时长换算和模型就绪判断。"""
import wave

import pytest

from backend import db, hashutil
from backend.ai.provider import provider_is_ready
from backend.ai.segment_analysis import _fmt_segments
from backend.api import parse_transcript_text


def test_text_segments_without_timestamps_can_be_saved_and_formatted():
    sem_id = db.create_semester("2026秋", "2026-09-01", "2027-01-31")
    course_id = db.create_course(sem_id, "测试课")
    session_id = db.get_or_create_session(course_id, "2026-09-09")

    segments = parse_transcript_text("老师介绍了第三章。\n\n请大家下周提交读书报告。")
    assert len(segments) == 1
    assert segments[0]["start_seconds"] is None
    assert segments[0]["end_seconds"] is None

    db.replace_segments(session_id, segments)
    saved = db.list_segments(session_id)
    assert saved[0]["start_seconds"] is None
    assert saved[0]["end_seconds"] is None
    assert "(无时间戳)" in _fmt_segments(saved)

    cols = {row["name"]: row for row in db.read_conn().execute("PRAGMA table_info(transcript_segments)")}
    assert cols["start_seconds"]["notnull"] == 0
    assert cols["end_seconds"]["notnull"] == 0


def test_audio_duration_uses_pyav_timebase(tmp_path):
    pytest.importorskip("av")
    audio = tmp_path / "one-second.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)

    assert hashutil.audio_duration_seconds(str(audio)) == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize(
    ("settings", "status", "expected"),
    [
        ({"ai_provider": "sidecar"},
         {"sidecar": {"available": True, "binary": True, "model": True}}, True),
        ({"ai_provider": "sidecar"},
         {"sidecar": {"available": True, "binary": True, "model": False}}, False),
        ({"ai_provider": "auto"},
         {"sidecar": {"available": False}, "ollama": {"available": True}, "openai": {}}, True),
    ],
)
def test_provider_readiness_uses_nested_status(settings, status, expected):
    assert provider_is_ready(settings, status) is expected
