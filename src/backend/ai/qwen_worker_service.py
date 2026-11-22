"""Standalone local Qwen3-ASR worker used by the frozen Windows app.

Protocol: one JSON object per stdin line, one JSON response per stdout line.
The worker loads the user's existing local model once, then handles every
recording in the current app session without downloading anything.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _write(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _language(value: str | None) -> str | None:
    if value == "zh":
        return "Chinese"
    if value == "en":
        return "English"
    return None


def _decode(path: str):
    import av
    import numpy as np

    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("录音文件中没有音频轨道")
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout="mono", rate=16000,
        )
        parts = []
        for frame in container.decode(stream):
            converted = resampler.resample(frame)
            converted = converted if isinstance(converted, list) else [converted]
            for out_frame in converted:
                parts.append(np.asarray(out_frame.to_ndarray(), dtype=np.float32).reshape(-1))
        converted = resampler.resample(None)
        converted = converted if isinstance(converted, list) else [converted]
        for out_frame in converted:
            parts.append(np.asarray(out_frame.to_ndarray(), dtype=np.float32).reshape(-1))
    if not parts:
        raise ValueError("录音文件为空或无法解码")
    wav = np.concatenate(parts).astype(np.float32, copy=False)
    return np.clip(wav, -1.0, 1.0, out=wav)


def _clear_cuda_cache() -> None:
    import torch

    torch.cuda.empty_cache()


def model_fingerprint(directory):
    """Fingerprint loaded model files once per worker, including tokenizer/config."""
    digest = hashlib.sha256()
    found = False
    for path in sorted(Path(directory).rglob('*')):
        if not path.is_file() or any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(directory).parts):
            continue
        if path.suffix not in {'.json', '.safetensors', '.bin', '.model', '.txt', '.tiktoken'}:
            continue
        found = True
        digest.update(str(path.relative_to(directory)).replace('\\', '/').encode('utf-8') + b'\0')
        file_digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                file_digest.update(block)
        digest.update(file_digest.digest())
    if not found:
        raise ValueError('未找到可校验的本地模型文件')
    return digest.hexdigest()


class ChunkCheckpoints:
    """Disposable local checkpoints; failures never make transcription fail."""
    def __init__(self, directory, identity):
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        self.directory = Path(directory) / key
        self.hits = 0

    def get(self, index, start, end):
        try:
            path = self.directory / f'{index:08d}.json'
            if path.stat().st_size > 1024 * 1024:
                return None
            saved = json.loads(path.read_text(encoding='utf-8'))
            data = saved['data']
            checksum = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
            if saved.get('sha256') != checksum or data.get('version') != 1:
                return None
            if any(type(data.get(field)) is not int for field in ('index', 'start_sample', 'end_sample')):
                return None
            if data.get('index') != index or data.get('start_sample') != start or data.get('end_sample') != end:
                return None
            if not isinstance(data.get('text'), str) or not isinstance(data.get('language'), str):
                return None
            self.hits += 1
            return {'start_seconds': round(start / 16000, 2), 'end_seconds': round(end / 16000, 2),
                    'text': data['text'], 'language': data['language']}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def put(self, index, start, end, row):
        temporary = None
        try:
            data = {'version': 1, 'index': index, 'start_sample': start, 'end_sample': end,
                    'text': row['text'], 'language': row['language']}
            checksum = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                             prefix='.chunk-', suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump({'data': data, 'sha256': checksum}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / f'{index:08d}.json')
        except (OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def checkpoint_for(wav, directory, model_identity, language, device, namespace=None):
    if not directory:
        return None
    # Hash the exact decoded input without copying the entire audio buffer.
    audio_hash = hashlib.sha256(memoryview(wav).cast('B')).hexdigest()
    identity = {'version': 1, 'audio': audio_hash, 'samples': int(wav.shape[0]),
                'model': model_identity, 'language': language, 'device': device,
                'dtype': 'float16' if device == 'cuda' else 'bfloat16',
                'sample_rate': 16000, 'chunk_seconds': 120, 'max_new_tokens': 1024, 'batch_size': 1,
                'runtime': runtime_versions(), 'namespace': namespace}
    return ChunkCheckpoints(directory, json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str))


def runtime_versions():
    versions = {}
    for package in ('qwen-asr', 'transformers', 'torch'):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = 'unknown'
    return versions


def transcribe_chunks(model, wav, language, device, batch_size=1, progress_cb=None, checkpoint=None):
    """Keep two-minute boundaries while batching adjacent inputs on the GPU."""
    import numpy as np

    sample_rate = 16000
    chunk_samples = 120 * sample_rate
    duration = float(wav.shape[0]) / sample_rate
    batch_size = max(1, min(int(batch_size), 4)) if device == "cuda" else 1
    start = 0
    results = []
    while start < wav.shape[0]:
        spans = [(pos, min(pos + chunk_samples, wav.shape[0]))
                 for pos in range(start, min(start + chunk_samples * batch_size, wav.shape[0]), chunk_samples)]
        rows = {lo: checkpoint.get(lo // chunk_samples, lo, hi) for lo, hi in spans} if checkpoint else {}
        missing = [(lo, hi) for lo, hi in spans if not rows.get(lo)]
        inputs = []
        for lo, hi in missing:
            chunk = wav[lo:hi]
            if chunk.shape[0] < 8000:
                chunk = np.pad(chunk, (0, 8000 - chunk.shape[0]))
            inputs.append((chunk, sample_rate))
        if missing:
            model.max_inference_batch_size = batch_size
            try:
                batch = model.transcribe(audio=inputs, language=language)
            except RuntimeError as exc:
                message = str(exc).lower()
                if device != "cuda" or "out of memory" not in message:
                    raise
                _clear_cuda_cache()
                if batch_size == 1:
                    # Do not restart the whole file on CPU after a batch memory failure.
                    raise RuntimeError("显存不足，当前片段无法完成。请关闭其他占用显卡的程序后重试。") from exc
                batch_size = max(1, batch_size // 2)
                continue
            if len(batch) != len(missing):
                raise ValueError("语音模型返回的片段数量不完整，已停止以避免录音内容缺失")
            for result, (lo, hi) in zip(batch, missing):
                row = {"start_seconds": round(lo / sample_rate, 2), "end_seconds": round(hi / sample_rate, 2),
                       "text": str(getattr(result, "text", "") or "").strip(),
                       "language": str(getattr(result, "language", "") or "")}
                if checkpoint:
                    checkpoint.put(lo // chunk_samples, lo, hi, row)
                rows[lo] = row
        results.extend(rows[lo] for lo, _hi in spans)
        start = spans[-1][1]
        if progress_cb:
            progress_cb(start / sample_rate, duration)
    return results


def main() -> int:
    # Keep import safe for the shared transcription helper and captured tests.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = parser.parse_args()

    import torch
    from qwen_asr import Qwen3ASRModel

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    model_dir = os.environ.get("CLASSROOM_ASSISTANT_QWEN_MODEL_DIR", "").strip()
    if not model_dir:
        raise RuntimeError("未找到 Qwen 模型目录")
    # bfloat16 keeps the CPU fallback usable on 16 GB machines.  float32
    # briefly needed several additional gigabytes while loading and could fail
    # even though the final model itself fits comfortably.
    dtype = torch.float16 if device == "cuda" else torch.bfloat16
    model = Qwen3ASRModel.from_pretrained(
        model_dir,
        dtype=dtype,
        device_map="cuda:0" if device == "cuda" else "cpu",
        max_inference_batch_size=1,
        max_new_tokens=1024,
        local_files_only=True,
    )
    loaded_model_identity = None

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("shutdown"):
                return 0
            if request.get("ready_check"):
                _write({"ok": True, "ready": True, "device": device})
                continue
            path = str(request.get("audio_path") or "")
            if not path or not os.path.isfile(path):
                raise ValueError("录音文件不存在")
            wav = _decode(path)
            duration_seconds = float(wav.shape[0]) / 16000.0
            checkpoint = None
            if request.get('checkpoint_dir'):
                try:
                    if loaded_model_identity is None:
                        loaded_model_identity = model_fingerprint(model_dir)
                    checkpoint = checkpoint_for(
                        wav, request['checkpoint_dir'], loaded_model_identity,
                        _language(request.get('language')), device, request.get('checkpoint_namespace'),
                    )
                except (OSError, ValueError, TypeError, BufferError):
                    pass
            def progress(end, duration):
                # Keep the parent alive on long recordings.  This is
                # deliberately a separate protocol message so the GUI can
                # update progress without waiting for the complete file.
                _write({
                    "kind": "progress",
                    "processed_seconds": round(end, 2),
                    "total_seconds": round(duration, 2),
                })
            results = transcribe_chunks(
                model, wav, _language(request.get("language")), device,
                progress_cb=progress, checkpoint=checkpoint,
            )
            _write({
                "ok": True,
                "device": device,
                "duration_seconds": round(duration_seconds, 2),
                "resumed_chunks": checkpoint.hits if checkpoint else 0,
                "results": results,
            })
        except Exception as exc:  # noqa: BLE001
            _write({"ok": False, "error": str(exc), "traceback": traceback.format_exc(limit=3)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        _write({"ok": False, "error": str(exc), "traceback": traceback.format_exc(limit=5)})
        raise
