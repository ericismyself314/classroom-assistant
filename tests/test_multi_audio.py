"""多录音分段上传与时间戳拼接回归测试。"""
import json
import os
import threading
import urllib.request

from backend import api as api_mod
from backend import db
from backend import pipeline as pipeline_mod


def _session(tmp_path):
    sem_id = db.create_semester("2026秋", "2026-09-01", "2027-01-31")
    course_id = db.create_course(sem_id, "测试课", language_mode="en")
    session_id = db.get_or_create_session(course_id, "2026-09-09")
    first = tmp_path / "part-1.wav"
    second = tmp_path / "part-2.wav"
    first.write_bytes(b"part-1")
    second.write_bytes(b"part-2")
    files = [
        {"path": str(first), "name": first.name, "bytes": first.stat().st_size,
         "duration": 2.0, "hash": "hash-1"},
        {"path": str(second), "name": second.name, "bytes": second.stat().st_size,
         "duration": 3.0, "hash": "hash-2"},
    ]
    db.update_session(
        session_id, audio_path=str(first), audio_hash="collection-hash",
        audio_duration_seconds=5.0, audio_manifest=json.dumps(files), source_type="audio",
    )
    return session_id, course_id, files


def test_audio_manifest_falls_back_to_legacy_single_file(tmp_path):
    sid, _, files = _session(tmp_path)
    session = db.get_session(sid)
    assert [f["name"] for f in db.audio_files_for_session(session)] == ["part-1.wav", "part-2.wav"]

    db.update_session(sid, audio_manifest=None, audio_path=files[0]["path"], audio_hash="hash-1")
    legacy = db.audio_files_for_session(db.get_session(sid))
    assert len(legacy) == 1
    assert legacy[0]["path"] == files[0]["path"]


def test_multiple_audio_segments_are_offset_in_order(tmp_path, monkeypatch):
    sid, course_id, files = _session(tmp_path)
    job_id = db.create_job(sid, "transcribe", status="running")
    calls = []

    monkeypatch.setattr(
        pipeline_mod.hashutil, "audio_duration_seconds",
        lambda path: 2.0 if path == files[0]["path"] else 3.0,
    )

    def fake_transcribe(path, **kwargs):
        calls.append(path)
        kwargs["progress_cb"](1.0)
        n = 0 if path == files[0]["path"] else 1
        return ([{
            "start_seconds": 0.5, "end_seconds": 1.0, "text": f"part {n + 1}",
            "original_text": f"part {n + 1}", "language": "en",
            "confidence": None, "is_uncertain": False,
        }], "en", "cpu")

    monkeypatch.setattr(pipeline_mod.whisper_worker, "transcribe", fake_transcribe)
    session = db.get_session(sid)
    course = db.get_course(course_id)
    segments, language, device = pipeline_mod._transcribe_audio_files(
        session, course, {"whisper_device": "cpu"}, "Qwen3-ASR-0.6B", None, job_id,
    )

    assert calls == [files[0]["path"], files[1]["path"]]
    assert [(s["start_seconds"], s["end_seconds"]) for s in segments] == [(0.5, 1.0), (2.5, 3.0)]
    assert language == "en"
    assert device == "cpu"
    assert db.get_session(sid)["audio_duration_seconds"] == 5.0
    assert "语音识别中" in (db.get_job(job_id)["stage"] or "")


def test_batch_upload_stores_all_files_and_starts_once(monkeypatch):
    sem_id = db.create_semester("2026秋", "2026-09-01", "2027-01-31")
    course_id = db.create_course(sem_id, "测试课")
    sid = db.get_or_create_session(course_id, "2026-09-09")
    server = api_mod.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []
    monkeypatch.setattr(api_mod.whisper_worker, "validate_model_installation", lambda size: {"ok": True})
    monkeypatch.setattr(api_mod, "detect_status", lambda settings: {})
    monkeypatch.setattr(api_mod, "provider_is_ready", lambda *args: True)
    monkeypatch.setattr(api_mod.hashutil, "is_audio_corrupted", lambda path: False)
    monkeypatch.setattr(api_mod.hashutil, "audio_duration_seconds", lambda path: 1.0)
    monkeypatch.setattr(api_mod, "start_pipeline", lambda *args: (calls.append(args) or ("job-1", None)))

    parts = [(f"part-{index}.wav", f"payload-{index}".encode()) for index in range(1, 7)]

    def upload(name, payload, index):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/sessions/{sid}/audio",
            data=payload, method="POST", headers={
                "X-Filename": name, "Content-Type": "application/octet-stream",
                "X-Batch-Upload": "1", "X-Batch-Index": str(index), "X-Batch-Count": str(len(parts)),
            },
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        results = [upload(name, payload, index) for index, (name, payload) in enumerate(parts)]
        first = results[0]
        last = results[-1]
        assert first["uploaded"] == 1
        assert all("job_id" not in result for result in results[:-1])
        assert last["job_id"] == "job-1"
        assert len(calls) == 1
        saved = db.audio_files_for_session(sid)
        assert [f["name"] for f in saved] == [name for name, _ in parts]
        assert [open(f["path"], "rb").read() for f in saved] == [payload for _, payload in parts]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/sessions/{sid}/audio/5"
        ) as response:
            assert response.read() == parts[-1][1]
    finally:
        server.shutdown()
        server.server_close()


def test_batch_upload_appends_to_existing_recordings(monkeypatch, tmp_path):
    sid, _, original_files = _session(tmp_path)
    server = api_mod.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api_mod.whisper_worker, "validate_model_installation", lambda size: {"ok": True})
    monkeypatch.setattr(api_mod, "detect_status", lambda settings: {})
    monkeypatch.setattr(api_mod, "provider_is_ready", lambda *args: True)
    monkeypatch.setattr(api_mod.hashutil, "is_audio_corrupted", lambda path: False)
    monkeypatch.setattr(api_mod.hashutil, "audio_duration_seconds", lambda path: 1.0)
    monkeypatch.setattr(api_mod, "start_pipeline", lambda *args: ("job-append", None))
    additions = [("part-3.wav", b"payload-3"), ("part-4.wav", b"payload-4")]

    def upload(name, payload, index):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/sessions/{sid}/audio",
            data=payload, method="POST", headers={
                "X-Filename": name, "Content-Type": "application/octet-stream",
                "X-Batch-Upload": "1", "X-Batch-Index": str(index),
                "X-Batch-Count": str(len(additions)),
            },
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        results = [upload(name, payload, index) for index, (name, payload) in enumerate(additions)]
        assert results[0]["uploaded"] == 1
        assert results[-1]["job_id"] == "job-append"
        saved = db.audio_files_for_session(sid)
        assert [f["name"] for f in saved] == ["part-1.wav", "part-2.wav", "part-3.wav", "part-4.wav"]
    finally:
        server.shutdown()
        server.server_close()


def test_deferred_audio_upload_can_delete_one_file_then_the_rest(monkeypatch):
    sem_id = db.create_semester("2026秋", "2026-09-01", "2027-01-31")
    course_id = db.create_course(sem_id, "暂存测试课")
    sid = db.get_or_create_session(course_id, "2026-09-10")
    server = api_mod.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api_mod.whisper_worker, "validate_model_installation",
                        lambda size: (_ for _ in ()).throw(AssertionError("暂存时不应校验模型")))
    monkeypatch.setattr(api_mod.hashutil, "is_audio_corrupted", lambda path: False)
    monkeypatch.setattr(api_mod.hashutil, "audio_duration_seconds", lambda path: 1.0)
    monkeypatch.setattr(api_mod, "start_pipeline",
                        lambda *args: (_ for _ in ()).throw(AssertionError("暂存时不应启动处理")))
    base = f"http://127.0.0.1:{server.server_address[1]}/api/sessions/{sid}/audio"

    try:
        parts = [("lecture-1.wav", b"staged-audio-1"), ("lecture-2.wav", b"staged-audio-2")]
        results = []
        for index, (name, payload) in enumerate(parts):
            request = urllib.request.Request(base, data=payload, method="POST", headers={
                "X-Filename": name, "Content-Type": "application/octet-stream",
                "X-Defer-Processing": "1", "X-Batch-Upload": "1",
                "X-Batch-Index": str(index), "X-Batch-Count": str(len(parts)),
            })
            with urllib.request.urlopen(request) as response:
                results.append(json.loads(response.read().decode("utf-8")))
        assert results[0]["uploaded"] == 1
        assert results[1]["staged"] is True
        assert results[1]["ready_to_process"] is True
        saved = db.audio_files_for_session(sid)
        assert len(saved) == 2 and all(os.path.exists(file["path"]) for file in saved)
        assert db.get_session(sid)["status"] == "ready_to_process"

        request = urllib.request.Request(base + "/0", method="DELETE")
        with urllib.request.urlopen(request) as response:
            deleted_one = json.loads(response.read().decode("utf-8"))
        assert deleted_one["file_count"] == 1
        remaining = db.audio_files_for_session(sid)
        assert [file["name"] for file in remaining] == ["lecture-2.wav"]
        assert not os.path.exists(saved[0]["path"])
        assert os.path.exists(remaining[0]["path"])
        assert db.get_session(sid)["status"] == "ready_to_process"

        request = urllib.request.Request(base, method="DELETE")
        with urllib.request.urlopen(request) as response:
            deleted = json.loads(response.read().decode("utf-8"))
        assert deleted["audio_removed"] is True
        assert db.get_session(sid)["audio_path"] is None
        assert db.get_session(sid)["status"] == "waiting_upload"
        assert not os.path.exists(remaining[0]["path"])
    finally:
        server.shutdown()
        server.server_close()
