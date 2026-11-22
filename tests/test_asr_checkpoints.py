import json
import hashlib
import os
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend.ai import qwen_worker_service as worker
from backend.ai import whisper_worker


def audio(seconds=241):
    wave = np.zeros(int(seconds * 16000), dtype=np.float32)
    for index, start in enumerate(range(0, len(wave), 120 * 16000)):
        wave[start:min(start + 120 * 16000, len(wave))] = index + 1
    return wave


class IndexedModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, language):
        indexes = [int(item[0][0]) for item in audio]
        self.calls.extend(indexes)
        return [SimpleNamespace(text=f'segment {i}', language='English') for i in indexes]


def test_interrupted_asr_reuses_successful_chunk_before_progress_notification(tmp_path):
    checkpoint = worker.ChunkCheckpoints(tmp_path, 'same-request')
    first = IndexedModel()
    def interrupt(end, total):
        raise InterruptedError('synthetic interruption')
    with pytest.raises(InterruptedError):
        worker.transcribe_chunks(first, audio(), 'English', 'cuda', checkpoint=checkpoint, progress_cb=interrupt)
    assert first.calls == [1]

    checkpoint = worker.ChunkCheckpoints(tmp_path, 'same-request')
    resumed = IndexedModel()
    progress = []
    rows = worker.transcribe_chunks(resumed, audio(), 'English', 'cuda', checkpoint=checkpoint,
        progress_cb=lambda end, total: progress.append(end))
    assert resumed.calls == [2, 3]
    assert [r['text'] for r in rows] == ['segment 1', 'segment 2', 'segment 3']
    assert [(r['start_seconds'], r['end_seconds']) for r in rows] == [(0, 120), (120, 240), (240, 241)]
    assert progress == [120, 240, 241]


def test_corrupt_or_misaligned_checkpoint_only_recomputes_its_own_chunk(tmp_path):
    checkpoint = worker.ChunkCheckpoints(tmp_path, 'same-request')
    expected = worker.transcribe_chunks(IndexedModel(), audio(), None, 'cuda', checkpoint=checkpoint)
    files = sorted(checkpoint.directory.glob('*.json'))
    assert len(files) == 3
    files[1].write_text('{broken', encoding='utf-8')
    model = IndexedModel()
    assert worker.transcribe_chunks(model, audio(), None, 'cuda', checkpoint=checkpoint) == expected
    assert model.calls == [2]

    payload = json.loads(files[0].read_text(encoding='utf-8'))
    payload['data']['end_sample'] += 1
    payload['sha256'] = hashlib.sha256(json.dumps(payload['data'], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    files[0].write_text(json.dumps(payload), encoding='utf-8')
    model = IndexedModel()
    assert worker.transcribe_chunks(model, audio(), None, 'cuda', checkpoint=checkpoint) == expected
    assert model.calls == [1]


def test_failed_model_response_is_not_saved_as_success(tmp_path):
    checkpoint = worker.ChunkCheckpoints(tmp_path, 'same-request')
    model = IndexedModel()
    original = model.transcribe
    def fail_second(**kwargs):
        if int(kwargs['audio'][0][0][0]) == 2:
            raise RuntimeError('synthetic inference failure')
        return original(**kwargs)
    model.transcribe = fail_second
    with pytest.raises(RuntimeError):
        worker.transcribe_chunks(model, audio(), None, 'cuda', checkpoint=checkpoint)
    assert len(list(checkpoint.directory.glob('*.json'))) == 1
    retry = IndexedModel()
    worker.transcribe_chunks(retry, audio(), None, 'cuda', checkpoint=checkpoint)
    assert retry.calls == [2, 3]


def test_checkpoint_identity_invalidates_audio_model_language_device_and_parameters(tmp_path, monkeypatch):
    wave = audio(1)
    def make(**kwargs):
        args = dict(wav=wave, directory=tmp_path / 'cache', model_identity='weights-a',
                    language='English', device='cuda', namespace={'epoch': 'a'})
        args.update(kwargs)
        return worker.checkpoint_for(**args).directory
    baseline = make()
    assert make() == baseline
    assert make(model_identity='weights-b') != baseline
    assert make(language='Chinese') != baseline
    assert make(device='cpu') != baseline
    assert make(namespace={'epoch': 'b'}) != baseline
    monkeypatch.setattr(worker, 'runtime_versions', lambda: {'qwen-asr': 'new-version'})
    assert make() != baseline
    monkeypatch.undo()
    wave[0] += 1
    assert make() != baseline


def test_model_fingerprint_changes_even_when_replacement_preserves_size_and_time(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    weights = model / 'model.safetensors'
    weights.write_bytes(b'weights-one')
    original = weights.stat()
    baseline = worker.model_fingerprint(model)
    weights.write_bytes(b'weights-two')
    os.utime(weights, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert worker.model_fingerprint(model) != baseline


def test_cache_write_failure_does_not_fail_recognition(tmp_path):
    root = tmp_path / 'not-a-directory'
    root.write_text('occupied', encoding='utf-8')
    checkpoint = worker.ChunkCheckpoints(root, 'same-request')
    model = IndexedModel()
    rows = worker.transcribe_chunks(model, audio(120), None, 'cpu', checkpoint=checkpoint)
    assert len(rows) == 1 and model.calls == [1]


def test_external_worker_passes_checkpoint_request_without_changing_original_input():
    external = whisper_worker._QwenExternalWorker(None, None, 'cuda')
    requests = []
    external.request = lambda payload, **kwargs: requests.append(payload) or {'results': []}
    external.transcribe_file('original.wav', 'en', checkpoint_dir='checkpoints', checkpoint_namespace={'epoch': 'retry'})
    assert requests == [{'audio_path': 'original.wav', 'language': 'en',
                         'checkpoint_dir': 'checkpoints', 'checkpoint_namespace': {'epoch': 'retry'}}]


def test_public_transcription_passes_checkpoint_options_through_gpu_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(whisper_worker, 'resolve_device', lambda pref: {'device': 'cuda', 'used_cuda': True})
    monkeypatch.setattr(whisper_worker, 'validate_qwen_model_installation', lambda: {'ok': True})
    def transcribe(path, **kwargs):
        calls.append(kwargs)
        if kwargs['device'] == 'cuda':
            raise RuntimeError('CUDA device unavailable')
        return [], 'en', 'cpu'
    monkeypatch.setattr(whisper_worker, '_transcribe_qwen', transcribe)
    whisper_worker.transcribe('original.wav', language='en', vocabulary='bond', checkpoint_dir='checkpoints',
                              checkpoint_namespace={'epoch': 'retry'})
    assert [call['device'] for call in calls] == ['cuda', 'cpu']
    assert all(call['checkpoint_dir'] == 'checkpoints' for call in calls)
    assert all(call['checkpoint_namespace'] == {'caller': {'epoch': 'retry'}, 'model_size': 'Qwen3-ASR-0.6B',
                                               'language_mode': 'en', 'vocabulary': 'bond'} for call in calls)


def test_external_worker_restart_resumes_disk_checkpoints_without_loading_real_models(tmp_path):
    # Use the real JSON-lines entry point with lightweight offline model doubles.
    (tmp_path / 'torch.py').write_text(
        "from types import SimpleNamespace\nfloat16='fp16'\nbfloat16='bf16'\n"
        "cuda=SimpleNamespace(is_available=lambda:True,empty_cache=lambda:None)\n", encoding='utf-8')
    (tmp_path / 'qwen_asr.py').write_text('''import os
from types import SimpleNamespace
class Qwen3ASRModel:
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        assert kwargs['max_inference_batch_size'] == 1
        assert kwargs['max_new_tokens'] == 1024
        assert kwargs['local_files_only'] is True
        return cls()
    def transcribe(self, audio, language):
        rows=[]
        for wav, rate in audio:
            index=int(wav[0])
            with open(os.environ['MODEL_CALLS'],'a') as stream:
                stream.write(str(index)+'\\n')
            if str(index)==os.environ.get('FAIL_AT'):
                raise RuntimeError('synthetic model failure')
            rows.append(SimpleNamespace(text='segment '+str(index),language='English'))
        return rows
''', encoding='utf-8')
    runner = tmp_path / 'runner.py'
    runner.write_text(f'''import importlib.util
import numpy as np
spec=importlib.util.spec_from_file_location('worker', {worker.__file__!r})
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def decode(path):
    return np.concatenate([np.full(120*16000,1,dtype=np.float32),np.full(120*16000,2,dtype=np.float32),np.full(16000,3,dtype=np.float32)])
module._decode=decode
raise SystemExit(module.main())
''', encoding='utf-8')
    model_dir = tmp_path / 'weights'
    model_dir.mkdir()
    (model_dir / 'model.safetensors').write_bytes(b'fake model fingerprint')
    original = tmp_path / 'original.wav'
    original.write_bytes(b'unchanged original recording')
    environment = os.environ.copy()
    calls = tmp_path / 'calls.txt'
    environment.update(CLASSROOM_ASSISTANT_QWEN_MODEL_DIR=str(model_dir), MODEL_CALLS=str(calls), FAIL_AT='2')
    payload = {'audio_path': str(original), 'language': 'en', 'checkpoint_dir': str(tmp_path / 'cache'),
               'checkpoint_namespace': {'epoch': 'retry'}}
    protocol = json.dumps(payload) + '\n' + json.dumps({'shutdown': True}) + '\n'
    def run():
        result = subprocess.run([sys.executable, str(runner), '--device', 'cuda'], input=protocol,
                                capture_output=True, text=True, encoding='utf-8', env=environment, timeout=20)
        assert result.returncode == 0, result.stderr
        return [json.loads(line) for line in result.stdout.splitlines()]
    first = run()
    assert first[-1]['ok'] is False
    assert calls.read_text().splitlines() == ['1', '2']
    calls.write_text('')
    environment.pop('FAIL_AT')
    second = run()
    assert second[-1]['ok'] is True and second[-1]['resumed_chunks'] == 1
    assert calls.read_text().splitlines() == ['2', '3']
    assert [row['text'] for row in second[-1]['results']] == ['segment 1', 'segment 2', 'segment 3']
    assert original.read_bytes() == b'unchanged original recording'
