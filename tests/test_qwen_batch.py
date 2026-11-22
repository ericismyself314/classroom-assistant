from types import SimpleNamespace

import numpy as np
import pytest

from backend.ai import qwen_worker_service as worker
from backend.ai import whisper_worker


def _audio(seconds):
    return np.zeros(int(seconds * 16000), dtype=np.float32)


class Model:
    def __init__(self, fail_sizes=()):
        self.max_inference_batch_size = 1
        self.calls = []
        self.fail_sizes = fail_sizes
        self.count = 0
        self.input_lengths = []

    def transcribe(self, audio, language):
        self.calls.append((len(audio), self.max_inference_batch_size, language))
        self.input_lengths.extend(len(item[0]) for item in audio)
        if len(audio) in self.fail_sizes:
            raise RuntimeError('CUDA out of memory')
        rows = []
        for _ in audio:
            rows.append(SimpleNamespace(text=f'part {self.count}', language='English'))
            self.count += 1
        return rows


def test_batch_preserves_order_tail_bounds_and_monotonic_progress():
    model = Model()
    progress = []
    rows = worker.transcribe_chunks(model, _audio(240.25), 'English', 'cuda',
                                    batch_size=2, progress_cb=lambda end, total: progress.append((end, total)))
    assert [row['text'] for row in rows] == ['part 0', 'part 1', 'part 2']
    assert [(row['start_seconds'], row['end_seconds']) for row in rows] == [(0, 120), (120, 240), (240, 240.25)]
    assert [call[0] for call in model.calls] == [2, 1]
    assert model.input_lengths == [120 * 16000, 120 * 16000, 8000]
    assert progress == [(240, 240.25), (240.25, 240.25)]


def test_cuda_oom_reduces_only_current_batch_without_repeating_completed_chunks(monkeypatch):
    model = Model()
    original = model.transcribe
    def fail_second_batch(audio, language):
        if model.count == 2 and len(audio) == 2:
            model.calls.append((2, 2, language))
            raise RuntimeError('CUDA out of memory')
        return original(audio, language)
    model.transcribe = fail_second_batch
    monkeypatch.setattr(worker, '_clear_cuda_cache', lambda: None)
    rows = worker.transcribe_chunks(model, _audio(480), 'Chinese', 'cuda', batch_size=2)
    assert [row['text'] for row in rows] == ['part 0', 'part 1', 'part 2', 'part 3']
    assert [call[0] for call in model.calls] == [2, 2, 1, 1]


def test_cpu_keeps_single_inference_even_if_batch_requested():
    model = Model()
    worker.transcribe_chunks(model, _audio(241), None, 'cpu', batch_size=4)
    assert [call[0] for call in model.calls] == [1, 1, 1]


def test_default_gpu_batch_is_single_after_real_recording_regression():
    model = Model()
    worker.transcribe_chunks(model, _audio(240), None, 'cuda')
    assert [call[0] for call in model.calls] == [1, 1]


def test_four_input_batch_reduces_to_two_and_handles_incomplete_tail(monkeypatch):
    model = Model(fail_sizes=(4,))
    monkeypatch.setattr(worker, '_clear_cuda_cache', lambda: None)
    rows = worker.transcribe_chunks(model, _audio(481), None, 'cuda', batch_size=4)
    assert [call[0] for call in model.calls] == [4, 2, 2, 1]
    assert [row['text'] for row in rows] == [f'part {i}' for i in range(5)]
    assert rows[-1]['end_seconds'] == 481


def test_single_input_memory_failure_does_not_restart_the_file_on_cpu(monkeypatch):
    model = Model(fail_sizes=(1, 2))
    monkeypatch.setattr(worker, '_clear_cuda_cache', lambda: None)
    with pytest.raises(RuntimeError, match='显存不足') as error:
        worker.transcribe_chunks(model, _audio(240), None, 'cuda', batch_size=2)
    assert [call[0] for call in model.calls] == [2, 1]
    assert not whisper_worker._looks_like_qwen_gpu_error(error.value)


def test_result_count_mismatch_fails_instead_of_silently_dropping_audio():
    model = Model()
    model.transcribe = lambda **kwargs: [SimpleNamespace(text='only one', language='English')]
    with pytest.raises(ValueError, match='数量'):
        worker.transcribe_chunks(model, _audio(240), None, 'cuda', batch_size=2)


def test_non_memory_exception_is_not_retried_as_a_smaller_batch():
    model = Model()
    def broken(**kwargs):
        raise RuntimeError('bad model input')
    model.transcribe = broken
    with pytest.raises(RuntimeError, match='bad model input'):
        worker.transcribe_chunks(model, _audio(240), None, 'cuda', batch_size=2)


def test_release_model_is_idempotent_and_preserves_cuda_capability(monkeypatch):
    closed = []
    monkeypatch.setattr(whisper_worker, '_qwen_external_worker', SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(whisper_worker, '_qwen_external_cuda_ready', True)
    whisper_worker.release_model()
    whisper_worker.release_model()
    assert closed == [True]
    assert whisper_worker._qwen_external_worker is None
    assert whisper_worker._qwen_external_cuda_ready is True


def test_cancel_during_external_progress_closes_worker_and_propagates():
    import io
    import queue
    messages = queue.Queue()
    messages.put('{"kind":"progress","processed_seconds":120}')
    process = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
    external = whisper_worker._QwenExternalWorker(process, messages, 'cuda')
    closed = []
    external.close = lambda: closed.append(True)
    def cancelled(_seconds):
        raise InterruptedError('已取消')
    with pytest.raises(InterruptedError):
        external.request({}, timeout=0.01, progress_cb=cancelled)
    assert closed == [True]
