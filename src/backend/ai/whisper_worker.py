"""本地语音识别（唯一支持 Qwen3-ASR-0.6B）。

要点：
- 在设置页下载模型；识别时只加载已经校验过的本地文件，不临时联网下载。
- 自动检测 CUDA；可用时优先 GPU（float16），否则回退 CPU（int8），不让无 GPU 导致整个应用不可用。
- 启用 VAD 静音过滤，降低长时间静音产生虚构文字的风险。
- 转写结果保留片段级时间戳；低可信/可能听不清的片段标记 is_uncertain。
- 优先保留老师实际使用的语言，不擅自翻译。

Qwen 在独立本地进程中加载，避免桌面进程混载不同 CUDA 原生运行库；GPU 失败时
会自动改用 CPU 重试，语音识别始终只走 Qwen3-ASR。
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import sys
import subprocess
import threading
import time

from ..config import get_config
from ..logutil import get_logger

LOG = get_logger("whisper")

# Keep the only supported ASR model in the user's data directory.
_QWEN_MODEL_ID = "Qwen3-ASR-0.6B"
_QWEN_MIN_MODEL_BYTES = 500 * 1024 * 1024
_qwen_model_lock = threading.Lock()
_qwen_external_worker = None
_qwen_external_cuda_ready: bool | None = None


class _QwenExternalWorker:
    """Keep Qwen's native runtime isolated from the frozen desktop process.

    The installed Python runtime contains the verified Qwen stack, so the
    desktop app talks to a small local JSON-lines worker instead. A worker is
    reused for all recordings on the same device in one app session.
    """

    def __init__(self, process, output_queue, requested_device: str):
        self.process = process
        self.output_queue = output_queue
        self.requested_device = requested_device
        self.device = requested_device

    def request(self, payload: dict, timeout: float = 900.0, progress_cb=None) -> dict:
        """Send one request and wait while allowing long recordings to progress.

        The worker processes a recording in two-minute chunks.  A fixed
        whole-file timeout made a long class fail after 15 minutes even though
        the child process was still healthy and transcribing.  Progress lines
        refresh the idle deadline; a genuinely stalled worker still fails
        after ``timeout`` seconds without progress.
        """
        if self.process.poll() is not None:
            raise RuntimeError(f"Qwen 语音服务已退出（exit={self.process.returncode}）")
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"无法连接 Qwen 语音服务：{exc}") from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Do not leave a child processing the same file after the GUI
                # has marked the job failed.  The next request will start a
                # fresh worker instead of consuming a stale response.
                self.close()
                raise RuntimeError("Qwen 语音服务长时间没有进度，请重试")
            try:
                line = self.output_queue.get(timeout=remaining)
            except queue.Empty as exc:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"Qwen 语音服务已退出（exit={self.process.returncode}）"
                    ) from exc
                self.close()
                raise RuntimeError("Qwen 语音服务长时间没有进度，请重试") from exc
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Qwen 语音服务返回了无效结果") from exc
            if result.get("kind") == "progress":
                processed = result.get("processed_seconds")
                if progress_cb is not None and processed is not None:
                    try:
                        progress_cb(float(processed))
                    except Exception:  # noqa: BLE001
                        pass
                # The timeout is an idle timeout, not a whole-recording cap.
                deadline = time.monotonic() + timeout
                continue
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "Qwen 语音服务识别失败"))
            return result

    def transcribe_file(self, audio_path: str, language: str | None, progress_cb=None) -> dict:
        return self.request(
            {"audio_path": audio_path, "language": language},
            progress_cb=progress_cb,
        )

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.stdin.write(json.dumps({"shutdown": True}) + "\n")
                self.process.stdin.flush()
                self.process.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass


def _qwen_worker_script() -> str:
    """Locate the worker script in source and PyInstaller layouts."""
    candidates = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "backend", "ai", "qwen_worker_service.py"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), "backend", "ai", "qwen_worker_service.py"))
    candidates.append(os.path.join(os.path.dirname(__file__), "qwen_worker_service.py"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise RuntimeError("未找到 Qwen 语音服务文件，请重新安装课程助手")


def _qwen_worker_python() -> str:
    """Find the local Python that has the verified Qwen runtime installed."""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.environ.get("CLASSROOM_ASSISTANT_PYTHON", ""),
        os.path.join(local_appdata, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_appdata, "Programs", "Python", "Python311", "python.exe"),
    ]
    if not getattr(sys, "frozen", False):
        candidates.insert(0, sys.executable)
    candidates.append(shutil.which("python") or "")
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise RuntimeError("未找到可运行 Qwen 语音模型的本地 Python 环境")


def _start_qwen_external_worker(device: str) -> _QwenExternalWorker:
    script = _qwen_worker_script()
    python = _qwen_worker_python()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["CLASSROOM_ASSISTANT_QWEN_MODEL_DIR"] = qwen_model_dir()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [python, script, "--device", device],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )
    output_queue: queue.Queue[str] = queue.Queue()

    def _read_stdout() -> None:
        try:
            for line in process.stdout:
                output_queue.put(line.strip())
        except Exception:  # noqa: BLE001
            pass

    def _read_stderr() -> None:
        try:
            for line in process.stderr:
                line = line.strip()
                if line:
                    LOG.info("Qwen 语音服务：%s", line)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_read_stdout, name="qwen-worker-out", daemon=True).start()
    threading.Thread(target=_read_stderr, name="qwen-worker-err", daemon=True).start()
    worker = _QwenExternalWorker(process, output_queue, device)
    try:
        ready = worker.request({"ready_check": True}, timeout=240.0)
    except Exception:
        worker.close()
        raise
    worker.device = str(ready.get("device") or device)
    LOG.info("Qwen3-ASR 独立语音服务已连接，设备=%s", worker.device)
    return worker


def _probe_external_qwen_cuda() -> bool:
    """Probe the system Qwen runtime without loading the 0.6B model."""
    global _qwen_external_cuda_ready
    if _qwen_external_cuda_ready is not None:
        return _qwen_external_cuda_ready
    try:
        python = _qwen_worker_python()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [python, "-c", "import torch; print('1' if torch.cuda.is_available() else '0')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creationflags,
        )
        _qwen_external_cuda_ready = result.returncode == 0 and result.stdout.strip().endswith("1")
    except Exception:  # noqa: BLE001
        _qwen_external_cuda_ready = False
    return _qwen_external_cuda_ready


def _close_qwen_external_worker() -> None:
    global _qwen_external_worker
    worker = _qwen_external_worker
    _qwen_external_worker = None
    if worker is not None:
        worker.close()


atexit.register(_close_qwen_external_worker)


def gpu_info(probe: bool = True) -> dict:
    """Report CUDA availability through the same PyTorch runtime as Qwen."""
    if not probe:
        return {"available": None, "device": "auto", "count": None,
                "name": "处理时自动检测", "runtime_ready": None,
                "usable": None, "reason": ""}
    cuda_ready = False
    device_count = 0
    try:
        if getattr(sys, "frozen", False):
            cuda_ready = _probe_external_qwen_cuda()
            device_count = int(cuda_ready)
        else:
            import importlib
            torch = importlib.import_module("torch")
            cuda_ready = bool(torch.cuda.is_available())
            if cuda_ready:
                device_count = max(1, int(torch.cuda.device_count()))
    except Exception:  # noqa: BLE001
        cuda_ready = False
        device_count = 0
    if cuda_ready:
        return {"available": True, "device": "cuda", "count": device_count,
                "name": "NVIDIA GPU（PyTorch CUDA）", "runtime_ready": True,
                "usable": True, "reason": ""}
    return {"available": False, "device": "cpu", "count": 0,
            "name": "CPU（Qwen PyTorch CUDA 不可用）", "runtime_ready": False,
            "usable": False, "reason": "未检测到可用的 PyTorch CUDA 设备"}


def resolve_device(pref: str = "auto") -> dict:
    """根据用户偏好 + 实际可用性，决定真实使用的设备。

    返回：
    - device: "cuda" 或 "cpu"
    - requested: 用户传入的偏好
    - used_cuda: 是否真的用了 GPU
    - reason: 为何没用 GPU（用了 GPU 时为空）
    - gpu: 完整 gpu_info()，便于前端展示
    """
    info = gpu_info()
    requested = (pref or "auto").lower().strip()
    if requested not in ("auto", "cpu", "cuda"):
        requested = "auto"

    if requested == "cpu":
        return {"device": "cpu", "requested": "cpu", "used_cuda": False,
                "reason": None, "gpu": info}
    if requested == "cuda":
        if info["usable"]:
            return {"device": "cuda", "requested": "cuda", "used_cuda": True,
                    "reason": None, "gpu": info}
        return {"device": "cpu", "requested": "cuda", "used_cuda": False,
                "reason": info["reason"] or "CUDA 不可用", "gpu": info}
    # auto
    if info["usable"]:
        return {"device": "cuda", "requested": "auto", "used_cuda": True,
                "reason": None, "gpu": info}
    return {"device": "cpu", "requested": "auto", "used_cuda": False,
            "reason": info["reason"] or "无可用 GPU", "gpu": info}


def qwen_model_dir(create: bool = True) -> str:
    """Return the local Qwen3-ASR model directory for the active data root."""
    d = os.path.join(get_config().dirs.speech_models, _QWEN_MODEL_ID)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def validate_qwen_model_installation() -> dict:
    """Validate the Qwen3-ASR files without attempting a network download."""
    repo_dir = qwen_model_dir(create=False)
    required = [
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "merges.txt",
        "model.safetensors",
    ]
    missing = []
    for name in required:
        path = os.path.join(repo_dir, name)
        try:
            size = os.path.getsize(path)
        except OSError:
            missing.append(name)
            continue
        if name == "model.safetensors" and size < _QWEN_MIN_MODEL_BYTES:
            missing.append(f"{name} (size={size} < {_QWEN_MIN_MODEL_BYTES})")
    reason = "" if not missing else ("no_snapshot" if not os.path.isdir(repo_dir) else "model_missing")
    out = {
        "ok": not missing,
        "reason": reason,
        "missing_files": missing,
        "repo_dir": repo_dir,
        "model_path": repo_dir,
        "size": _QWEN_MODEL_ID,
        "backend": "qwen3-asr",
        "detail": "Qwen3-ASR-0.6B 本地模型完整可用" if not missing else (
            "Qwen3-ASR-0.6B 文件缺失或不完整：" + ", ".join(missing)
        ),
    }
    return out


def is_model_downloaded(size: str = _QWEN_MODEL_ID) -> bool:
    """Only the supported Qwen ASR model counts as ready."""
    return size == _QWEN_MODEL_ID and bool(validate_qwen_model_installation()["ok"])


def validate_model_installation(size: str) -> dict:
    """Compatibility name for callers; never validates legacy Whisper caches."""
    if size == _QWEN_MODEL_ID:
        return validate_qwen_model_installation()
    return {"ok": False, "size": size, "reason": "unsupported_model",
            "detail": "语音识别只支持 Qwen3-ASR-0.6B"}


def cleanup_broken_model(size: str) -> dict:
    """删除唯一 Qwen 模型目录中的损坏下载；不删除完整模型。"""
    if size != _QWEN_MODEL_ID:
        raise ValueError("语音识别只支持 Qwen3-ASR-0.6B")
    if validate_qwen_model_installation()["ok"]:
        return {"size": size, "removed": [], "now_downloaded": True}

    speech_root = os.path.abspath(get_config().dirs.speech_models)
    model_dir = os.path.abspath(qwen_model_dir(create=False))
    is_junction = getattr(os.path, "isjunction", lambda _path: False)
    if (os.path.islink(model_dir) or is_junction(model_dir) or
            os.path.normcase(os.path.dirname(model_dir)) != os.path.normcase(speech_root)):
        raise ValueError("Qwen 模型目录不安全，已停止清理")
    removed = []
    if os.path.isdir(model_dir):
        shutil.rmtree(model_dir)
        removed.append(model_dir)
    return {"size": size, "removed": removed, "now_downloaded": is_model_downloaded(size)}


def _looks_like_qwen_gpu_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in (
        "cuda", "cublas", "cudnn", "out of memory", "device_map", "gpu",
    ))


def _qwen_language(language: str | None) -> str | None:
    if language == "zh":
        return "Chinese"
    if language == "en":
        return "English"
    return None


def _qwen_language_code(value: str | None, requested: str | None) -> str | None:
    if requested == "mix":
        return "mix"
    if requested in {"zh", "en"}:
        return requested
    low = str(value or "").lower()
    if "chinese" in low and "english" in low:
        return "mix"
    if "chinese" in low or "cantonese" in low or "中文" in low:
        return "zh"
    if "english" in low or "英文" in low:
        return "en"
    return None


def _get_qwen_model(device: str):
    """Load Qwen3-ASR once per device and reuse it for multi-recording jobs."""
    global _qwen_external_worker, _qwen_external_cuda_ready
    # Always isolate PyTorch, including source/development runs.  A native CUDA
    # abort must never take the desktop process down with it.
    with _qwen_model_lock:
        desired_device = "cpu" if device == "cpu" else "cuda"
        worker_live = (_qwen_external_worker is not None and
                       _qwen_external_worker.process.poll() is None)
        worker_matches = (worker_live and _qwen_external_worker.device == desired_device)
        if not worker_matches:
            if _qwen_external_worker is not None:
                _qwen_external_worker.close()
                _qwen_external_worker = None
            requested = "cpu" if desired_device == "cpu" else "auto"
            try:
                _qwen_external_worker = _start_qwen_external_worker(requested)
                if requested == "auto" and _qwen_external_worker.device != "cuda":
                    _qwen_external_cuda_ready = False
            except Exception:
                if requested == "cpu":
                    raise
                LOG.warning("Qwen CUDA 服务启动失败，正在改用低内存 CPU 模式")
                _qwen_external_cuda_ready = False
                _qwen_external_worker = _start_qwen_external_worker("cpu")
        return _qwen_external_worker

def _load_audio_16k(audio_path: str):
    """Decode supported classroom audio without librosa/numba dependencies."""
    import av
    import numpy as np

    with av.open(audio_path) as container:
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
    return np.clip(wav, -1.0, 1.0)


def split_qwen_segment(text: str, start_seconds: float, end_seconds: float,
                       max_chars: int = 240) -> list[dict]:
    """Turn Qwen's coarse two-minute result into readable source segments.

    Qwen ASR does not return word timestamps.  We split at punctuation and
    allocate the original time span proportionally.  Keeping each source under
    300 characters also makes translation and evidence verification reliable.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    sentences = [p.strip() for p in re.split(
        r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z0-9])", text
    ) if p.strip()] or [text]
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = max(sentence.rfind("，", 0, max_chars),
                      sentence.rfind(",", 0, max_chars),
                      sentence.rfind(" ", 0, max_chars))
            if cut < max_chars // 2:
                cut = max_chars
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        combined = (current + (" " if current and sentence else "") + sentence).strip()
        if current and len(combined) > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = combined
    if current:
        parts.append(current)

    total_chars = max(1, sum(len(part) for part in parts))
    cursor = float(start_seconds)
    duration = max(0.0, float(end_seconds) - float(start_seconds))
    result = []
    for index, part in enumerate(parts):
        part_end = float(end_seconds) if index == len(parts) - 1 else (
            cursor + duration * len(part) / total_chars
        )
        result.append({"start_seconds": round(cursor, 2),
                       "end_seconds": round(part_end, 2), "text": part})
        cursor = part_end
    return result


def _transcribe_qwen(audio_path: str, language: str | None = None,
                     device: str = "cpu", progress_cb=None):
    """Transcribe a local file with Qwen3-ASR using coarse timestamped chunks.

    Qwen3-ASR returns one text result per input, while the rest of the app
    consumes timestamped segments.  Splitting into two-minute windows keeps
    the existing notes/evidence pipeline useful without requiring the optional
    Qwen forced-aligner model.
    """
    import numpy as np

    model = _get_qwen_model(device)
    if isinstance(model, _QwenExternalWorker):
        payload = model.transcribe_file(audio_path, language, progress_cb=progress_cb)
        out = []
        detected = None
        for item in payload.get("results", []):
            text = str(item.get("text") or "").strip()
            detected = _qwen_language_code(item.get("language"), language) or detected
            if not text:
                continue
            for part in split_qwen_segment(
                text, float(item.get("start_seconds") or 0),
                float(item.get("end_seconds") or 0),
            ):
                out.append({**part, "original_text": part["text"],
                            "language": "mix" if language == "mix" else (detected or language),
                            "confidence": None, "is_uncertain": False})
        if not out:
            raise ValueError("这段录音中没有识别到清晰的人声。请先播放录音确认有声音，再尝试重新转写。")
        detected = "mix" if language == "mix" else detected
        check_detected_language(detected, language)
        if progress_cb:
            duration = float(payload.get("duration_seconds") or 0)
            if duration > 0:
                progress_cb(duration)
        LOG.info("Qwen 转写完成：%d 段，语言=%s，设备=%s", len(out), detected, model.device)
        return out, detected, model.device

    SAMPLE_RATE = 16000

    wav = np.asarray(_load_audio_16k(audio_path), dtype=np.float32)
    if wav.size == 0:
        raise ValueError("录音文件为空")
    duration = float(wav.shape[0]) / float(SAMPLE_RATE)
    chunk_samples = int(120 * SAMPLE_RATE)
    qwen_lang = _qwen_language(language)
    out = []
    detected = None
    for start in range(0, wav.shape[0], chunk_samples):
        end = min(wav.shape[0], start + chunk_samples)
        chunk = wav[start:end]
        if chunk.shape[0] < int(0.5 * SAMPLE_RATE):
            chunk = np.pad(chunk, (0, int(0.5 * SAMPLE_RATE) - chunk.shape[0]))
        result = model.transcribe(
            audio=(chunk, SAMPLE_RATE),
            language=qwen_lang,
        )[0]
        text = str(getattr(result, "text", "") or "").strip()
        detected = _qwen_language_code(getattr(result, "language", None), language) or detected
        if progress_cb:
            progress_cb(float(end) / float(SAMPLE_RATE))
        if not text:
            continue
        for part in split_qwen_segment(
            text, start / float(SAMPLE_RATE), end / float(SAMPLE_RATE),
        ):
            out.append({**part, "original_text": part["text"],
                        "language": "mix" if language == "mix" else (detected or language),
                        "confidence": None, "is_uncertain": False})
    if not out:
        raise ValueError("这段录音中没有识别到清晰的人声。请先播放录音确认有声音，再尝试重新转写。")
    detected = "mix" if language == "mix" else detected
    check_detected_language(detected, language)
    LOG.info("Qwen 转写完成：%d 段，语言=%s，设备=%s", len(out), detected, device)
    return out, detected, device


def recognition_options(language=None, vocabulary="") -> dict:
    mode = (language or "auto").lower()
    if mode not in {"auto", "zh", "en", "mix"}:
        raise ValueError("请选择授课语言：英文、中文、中英混合或自动识别。")
    return {"language": mode if mode in {"zh", "en"} else None,
            "multilingual": mode == "mix",
            "hotwords": str(vocabulary or "").strip()[:800] or None,
            "language_detection_segments": 5,
            "language_detection_threshold": 1.0}


def check_detected_language(language, requested=None):
    if (requested or "auto") in {"auto", "mix"} and language not in {"en", "zh", "mix"}:
        raise ValueError(
            f"自动识别得到的语言（{language}）与中英文课堂不符，已暂停，避免生成错误笔记。"
            "请在「识别设置」中选择老师实际使用的语言，再点「重新转写」。原录音和旧结果仍保留。")


def ensure_ready(device_pref: str = "auto") -> dict:
    """Really load Qwen once and report the actual device, without using audio."""
    validation = validate_qwen_model_installation()
    if not validation["ok"]:
        raise RuntimeError(validation["detail"])
    plan = resolve_device(device_pref)
    model = _get_qwen_model(plan["device"])
    device = model.device if isinstance(model, _QwenExternalWorker) else plan["device"]
    return {"ready": True, "device": device, "detail": validation["detail"]}


def transcribe(audio_path: str, size: str = _QWEN_MODEL_ID, language: str | None = None,
               device_pref: str = "auto", progress_cb=None,
               on_retry=None, vocabulary=""):
    """转写一段音频。

    新增 `device_pref` 与 `on_retry`：
    - device_pref: "auto" / "cpu" / "cuda"。auto 优先 GPU，不可用时 CPU。
    - on_retry(reason, exc): GPU 失败回退 CPU 时回调，便于前端提示
      "已自动切换到 CPU 重新识别"。
    """
    if size != _QWEN_MODEL_ID:
        raise ValueError("语音识别只支持 Qwen3-ASR-0.6B")
    choice = resolve_device(device_pref)
    device = choice["device"]

    if choice["used_cuda"]:
        LOG.info("使用 Qwen3-ASR，设备=GPU（偏好=%s）", device_pref)
    else:
        reason = choice.get("reason") or "无可用 GPU"
        LOG.info("使用 Qwen3-ASR，设备=CPU（GPU 不可用：%s；偏好=%s）",
                 reason, device_pref)

    qwen_validation = validate_qwen_model_installation()
    if qwen_validation["ok"]:
        try:
            return _transcribe_qwen(
                audio_path, language=language, device=device, progress_cb=progress_cb,
            )
        except Exception as e:  # noqa: BLE001
            if device == "cuda" and _looks_like_qwen_gpu_error(e):
                LOG.warning("Qwen GPU 转写失败，自动回退 CPU 重试一次：%s", e)
                if on_retry is not None:
                    try:
                        on_retry("cuda_failed_fallback_cpu", str(e))
                    except Exception:  # noqa: BLE001
                        pass
                return _transcribe_qwen(
                    audio_path, language=language, device="cpu", progress_cb=progress_cb,
                )
            raise

    # A missing Qwen model must be visible and actionable in Settings.
    raise RuntimeError(qwen_validation["detail"])

def segment_is_uncertain(seg):
    """Conservative signal, not an assertion that unflagged text is correct."""
    return (float(getattr(seg, 'no_speech_prob', 0) or 0) > 0.5 or
            float(getattr(seg, 'avg_logprob', 0) or 0) < -0.8 or
            float(getattr(seg, 'compression_ratio', 0) or 0) > 2.4)
