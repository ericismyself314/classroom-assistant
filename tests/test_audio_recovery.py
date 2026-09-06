"""Focused audio recovery regressions; no models, network, or real app DB used."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend import api
from backend.ai import whisper_worker as worker
from backend.ai import segment_analysis


def test_partial_cuda_runtime_is_not_ready():
    def load(name):
        if "cudnn" in name:
            raise OSError("missing cuDNN")
        return object()
    with patch.object(worker, "_cuda_ready", None), \
         patch.object(worker, "_register_nvidia_dll_dirs"), \
         patch.object(worker.ctypes, "WinDLL", side_effect=load, create=True):
        assert worker.cuda_runtime_ready() is False


def test_retry_marks_session_processing_before_thread_start():
    events = []
    with patch.object(api.db, "active_jobs", return_value=[]), \
         patch.object(api.db, "get_session", return_value={'id': 'session'}), \
         patch.object(api.db, "create_job", return_value="job"), \
         patch.object(api.db, "update_session", side_effect=lambda *a, **kw: events.append(kw)), \
         patch.object(api.threading, "Thread") as thread:
        thread.return_value.start.side_effect = lambda: events.append("started")
        assert api.start_pipeline("session", "transcribe") == ("job", None)
        assert events == [{"status": "file_check", "processing_error": None}, "started"]


def test_gpu_failure_retries_cpu_and_uses_validated_local_snapshot():
    calls = []
    retries = []

    class Model:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))
            self.device = kwargs["device"]

        def transcribe(self, *args, **kwargs):
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            segment = SimpleNamespace(start=1, end=2, text="本地识别", no_speech_prob=0, avg_logprob=0)
            return iter([segment]), SimpleNamespace(language="zh")

    with patch.object(worker, "resolve_device", return_value={"device": "cuda", "used_cuda": True}), \
         patch.object(worker, "validate_model_installation", return_value={"ok": True, "model_path": "verified-snapshot"}), \
         patch.object(worker, "_cache_dir", return_value="unused-cache"), \
         patch.object(worker, "models_root", return_value="unused-models"), \
         patch.object(worker, "_cuda_ready", None), \
         patch.dict(os.environ, {}), \
         patch("faster_whisper.WhisperModel", Model):
        segments, language, device = worker.transcribe("unused-audio", on_retry=lambda *a: retries.append(a))
    assert device == "cpu" and language == "zh" and segments[0]["text"] == "本地识别"
    assert len(retries) == 1 and len(calls) == 2
    assert all(p == "verified-snapshot" and kw["local_files_only"] for p, kw in calls)
    assert calls[1][1]["compute_type"] == "int8"


def test_empty_audio_is_not_reported_as_success():
    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([]), SimpleNamespace(language="zh")

    with patch.object(worker, "resolve_device", return_value={"device": "cpu", "used_cuda": False}), \
         patch.object(worker, "validate_model_installation", return_value={"ok": True, "model_path": "verified"}), \
         patch.object(worker, "_cache_dir", return_value="unused-cache"), \
         patch.object(worker, "models_root", return_value="unused-models"), \
         patch.dict(os.environ, {}), \
         patch("faster_whisper.WhisperModel", Model):
        try:
            worker.transcribe("unused-audio")
        except ValueError as error:
            assert "没有识别到清晰的人声" in str(error)
        else:
            raise AssertionError("Empty audio was accepted")


def test_validator_tries_legacy_after_broken_current():
    with patch.object(worker, "models_root", return_value="current"), \
         patch.object(worker, "_cache_dir", return_value="legacy"), \
         patch.object(worker.os.path, "isdir", return_value=True), \
         patch.object(worker.os.path, "isfile", return_value=False), \
         patch.object(worker.os, "listdir", return_value=["snapshot"]), \
         patch.object(worker.os.path, "getmtime", return_value=1), \
         patch("builtins.open", side_effect=FileNotFoundError), \
         patch.object(worker, "_validate_model_snapshot", side_effect=[
             {"ok": False, "reason": "blob_truncated"},
             {"ok": True, "model_path": "legacy-valid"}]):
        assert worker.validate_model_installation("small")["model_path"] == "legacy-valid"


def test_analysis_formats_prompt_and_recovers_invalid_json():
    calls = []

    class Provider:
        def chat(self, system, user, **kwargs):
            calls.append(system)
            if len(calls) == 1:
                return "invalid"
            if len(calls) == 2:
                return '{"topics":[{"title":"实际课程内容","summary":"这是实际课堂内容","points":[{"kind":"concept","text":"课堂内容已整理"}]}]}'
            return '{"supported_ids":["0"]}'

    overview, items = segment_analysis.analyze_session(Provider(),
        [{"text": "这是实际课堂内容", "start_seconds": 0, "end_seconds": 10}],
        {"session_date": "2026-09-01"})
    assert len(calls) >= 2 and "{{" not in calls[0] and "{class_date}" not in calls[0]
    assert overview["topics"] and items == []
    assert overview["notes"]["version"] == 3
    assert overview["notes"]["timeline"] and overview["notes"]["knowledge"]


def test_analysis_invalid_chunk_falls_back_to_transcript():
    class Provider:
        def chat(self, *args, **kwargs):
            return "invalid"
    overview, items = segment_analysis.analyze_session(
        Provider(), [{"text": "课堂内容"}], {"session_date": "2026-09-01"})
    assert overview["topics"][0]["summary"] == "课堂内容" and items == []


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print("PASS", name, flush=True)
