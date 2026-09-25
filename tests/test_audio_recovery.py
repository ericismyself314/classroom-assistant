"""Focused audio recovery regressions; no models, network, or real app DB used."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend import api
from backend.ai import whisper_worker as worker
from backend.ai import segment_analysis


def test_qwen_gpu_failure_restarts_worker_in_cpu_mode():
    class Process:
        def poll(self):
            return None

    class Worker:
        def __init__(self, device):
            self.device = device
            self.process = Process()
            self.closed = False

        def close(self):
            self.closed = True

    gpu_worker = Worker("cuda")
    cpu_worker = Worker("cpu")
    with patch.object(worker, "_qwen_external_worker", gpu_worker), \
         patch.object(worker, "_start_qwen_external_worker", return_value=cpu_worker) as start:
        assert worker._get_qwen_model("cpu") is cpu_worker
    assert gpu_worker.closed
    start.assert_called_once_with("cpu")


def test_gpu_probe_uses_qwen_pytorch_runtime():
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, device_count=lambda: 2))
    with patch.object(worker.sys, "frozen", False, create=True), \
         patch("importlib.import_module", return_value=torch):
        info = worker.gpu_info()
    assert info["usable"] is True and info["count"] == 2
    assert info["name"] == "NVIDIA GPU（PyTorch CUDA）"


def test_cleanup_removes_only_partial_qwen_model(tmp_path):
    speech_root = tmp_path / "speech"
    model_dir = speech_root / "Qwen3-ASR-0.6B"
    model_dir.mkdir(parents=True)
    (model_dir / "partial.bin").write_bytes(b"partial")
    other_model = speech_root / "another-model"
    other_model.mkdir()
    marker = other_model / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    config = SimpleNamespace(dirs=SimpleNamespace(speech_models=str(speech_root)))

    with patch.object(worker, "get_config", return_value=config), \
         patch.object(worker, "validate_qwen_model_installation", return_value={"ok": False}):
        result = worker.cleanup_broken_model("Qwen3-ASR-0.6B")
    assert result["removed"] == [str(model_dir)]
    assert not model_dir.exists() and marker.read_text(encoding="utf-8") == "keep"


def test_cleanup_does_not_remove_complete_or_unsupported_model(tmp_path):
    with patch.object(worker, "validate_qwen_model_installation", return_value={"ok": True}):
        result = worker.cleanup_broken_model("Qwen3-ASR-0.6B")
    assert result == {"size": "Qwen3-ASR-0.6B", "removed": [], "now_downloaded": True}
    try:
        worker.cleanup_broken_model("small")
        assert False, "其他语音模型不应进入清理路径"
    except ValueError as exc:
        assert "Qwen3-ASR-0.6B" in str(exc)


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
