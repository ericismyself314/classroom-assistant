"""Standalone local Qwen3-ASR worker used by the frozen Windows app.

Protocol: one JSON object per stdin line, one JSON response per stdout line.
The worker loads the user's existing local model once, then handles every
recording in the current app session without downloading anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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
    return np.clip(np.concatenate(parts).astype(np.float32, copy=False), -1.0, 1.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = parser.parse_args()

    import numpy as np
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
            chunk_samples = 120 * 16000
            duration_seconds = float(wav.shape[0]) / 16000.0
            results = []
            for start in range(0, wav.shape[0], chunk_samples):
                end = min(wav.shape[0], start + chunk_samples)
                chunk = wav[start:end]
                if chunk.shape[0] < 8000:
                    chunk = np.pad(chunk, (0, 8000 - chunk.shape[0]))
                result = model.transcribe(
                    audio=(chunk, 16000),
                    language=_language(request.get("language")),
                )[0]
                results.append({
                    "start_seconds": round(start / 16000.0, 2),
                    "end_seconds": round(end / 16000.0, 2),
                    "text": str(getattr(result, "text", "") or "").strip(),
                    "language": str(getattr(result, "language", "") or ""),
                })
                # Keep the parent alive on long recordings.  This is
                # deliberately a separate protocol message so the GUI can
                # update progress without waiting for the complete file.
                _write({
                    "kind": "progress",
                    "processed_seconds": round(end / 16000.0, 2),
                    "total_seconds": round(duration_seconds, 2),
                })
            _write({
                "ok": True,
                "device": device,
                "duration_seconds": round(duration_seconds, 2),
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
