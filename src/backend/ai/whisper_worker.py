"""本地语音识别（faster-whisper）。

要点：
- 在设置页下载模型；识别时只加载已经校验过的本地文件，不临时联网下载。
- 自动检测 CUDA；可用时优先 GPU（float16），否则回退 CPU（int8），不让无 GPU 导致整个应用不可用。
- 启用 VAD 静音过滤，降低长时间静音产生虚构文字的风险。
- 转写结果保留片段级时间戳；低可信/可能听不清的片段标记 is_uncertain。
- 优先保留老师实际使用的语言，不擅自翻译。

历史坑（2026-09）：
- `ctranslate2.get_cuda_device_count()` 只检测驱动/设备，**不代表运行库可用**。
  在 PyInstaller 打包后的环境里，没有 nvidia-cublas-cu12 / nvidia-cudnn-cu12 等
  pip 包的 DLL，ct2 仍报告 1 个设备，但实际 transcribe 时抛
  `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`，
  整次识别静默失败。
- 修复：增加 `cuda_runtime_ready()` 真实可用性探测（加载 cublas 关键 DLL），
  并在 `transcribe()` 中把"GPU 失败 → CPU 重试"作为兜底，保证上传录音不因
  环境差异而崩。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading

from ..config import get_config
from ..logutil import get_logger

LOG = get_logger("whisper")

# CTranslate2 的 tiny 模型也远大于 10 MiB。这个下限不是拿来替代模型
# 自身的加载校验，而是避免残留空目录、测试占位文件和明显截断的下载被误报为
# "已下载"。
_MIN_MODEL_BIN_BYTES = 10 * 1024 * 1024


def _repo_cache_names(size: str) -> list[str]:
    """Return HuggingFace cache directory names used by faster-whisper.

    Most official converted models live under Systran, while faster-whisper
    maps large-v3-turbo/turbo to mobiuslabsgmbh.  Keep the historical Systran
    candidate as a fallback so an older manual import is not lost.
    """
    names = []
    if size in {"large-v3-turbo", "turbo"}:
        names.append("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
    names.append("models--Systran--faster-whisper-" + size)
    return list(dict.fromkeys(names))


# ---------------------------------------------------------------------------
# CUDA 运行库真实可用性探测
# ---------------------------------------------------------------------------
# get_cuda_device_count() 报告 0/1 都不能直接当"能跑 GPU"用——前者可能是装了
# CPU 版 ctranslate2，后者多见 PyInstaller 打包时漏掉 nvidia-cublas/cudnn 的
# DLL。下面的探测既覆盖"关键 DLL 是否能被 LoadLibrary 加载"（毫秒级，失败 =
# 立即 CPU），又留出运行时兜底处理"探测过但仍失败"的边角情况。
_cuda_ready: bool | None = None
_cuda_probe_lock = threading.Lock()
_dll_directory_handles: dict[str, object] = {}
_cuda_libraries: list[object] = []

# 探测重点：cuBLAS 是 CTranslate2 GPU 推理必用的；cuBLASLt / cuDNN / cuRAND
# 在不同版本下或必须或可选，缺一会被 ct2 内部用 std::abort 拉崩。
_CUDA_DLL_CANDIDATES = (
    "cublasLt64_12.dll",
    "cublas64_12.dll",
    # CTranslate2 >= 4.5 的 Whisper 需要 CUDA 12 和 cuDNN 9。
    # 只找到任意一个 cuBLAS DLL 不能证明显卡识别可用。
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
)


def _register_nvidia_dll_dirs() -> None:
    """把 site-packages/nvidia/*/bin 加进 Windows DLL 搜索路径。

    用户若额外安装了 `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` 等 pip 包，
    DLL 不会自动被系统 PATH 找到，需要通过 `os.add_dll_directory` 注册。
    """
    if sys.platform != "win32":
        return
    try:
        import site
        from sysconfig import get_paths

        candidates = list(site.getsitepackages() or [])
        purelib = get_paths().get("purelib")
        if purelib:
            candidates.append(purelib)
        if getattr(sys, "frozen", False):
            candidates.append(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
        seen: set[str] = set()
        for base in candidates:
            nvidia_root = os.path.join(base, "nvidia")
            if not os.path.isdir(nvidia_root):
                continue
            for sub in os.listdir(nvidia_root):
                bin_dir = os.path.join(nvidia_root, sub, "bin")
                if os.path.isdir(bin_dir) and bin_dir not in seen:
                    try:
                        if bin_dir not in _dll_directory_handles:
                            # 句柄必须活到进程结束，否则目录会立刻从搜索路径移除。
                            _dll_directory_handles[bin_dir] = os.add_dll_directory(bin_dir)
                        seen.add(bin_dir)
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001
        pass


def cuda_runtime_ready() -> bool:
    """cuBLAS 等 GPU 推理关键 DLL 实际可被加载。"""
    global _cuda_ready
    if _cuda_ready is not None:
        return _cuda_ready
    with _cuda_probe_lock:
        if _cuda_ready is not None:
            return _cuda_ready
        if sys.platform != "win32":
            # 非 Windows 留给真实 transcribe 时再判定（Linux/Mac 行为更线性）
            _cuda_ready = True
            return _cuda_ready
        _register_nvidia_dll_dirs()
        ok = True
        loaded = []
        for name in _CUDA_DLL_CANDIDATES:
            try:
                loaded.append(ctypes.WinDLL(name))
            except OSError:
                ok = False
                break
        if ok:
            _cuda_libraries.extend(loaded)
        _cuda_ready = ok
        if not ok:
            LOG.warning(
                "显卡识别所需的 CUDA/cuDNN 运行库不完整；已自动改用 CPU，仍可本地识别录音。"
            )
        return _cuda_ready


def invalidate_cuda_ready() -> None:
    """让下一次调用重新探测（用于用户安装好 nvidia-* 包后）。"""
    global _cuda_ready
    with _cuda_probe_lock:
        _cuda_ready = None


def gpu_info() -> dict:
    """驱动/设备探测 + 运行库真实可用性。

    返回字段：
    - available: ctranslate2.get_cuda_device_count() > 0
    - runtime_ready: cublas 等关键 DLL 可被加载
    - usable: 上述两者都为 True（前端「可启用 GPU」可据此显示）
    - device/count/name: 给前端展示用
    """
    base = {"available": False, "device": "cpu", "count": 0, "name": "CPU",
            "runtime_ready": False, "usable": False, "reason": ""}
    try:
        from ctranslate2 import get_cuda_device_count
        n = get_cuda_device_count()
        if n > 0:
            base = {"available": True, "device": "cuda", "count": n,
                    "name": f"NVIDIA GPU（CUDA 设备数 = {n}）",
                    "runtime_ready": False, "usable": False, "reason": ""}
    except Exception:  # noqa: BLE001
        pass
    ready = cuda_runtime_ready()
    base["runtime_ready"] = ready
    base["usable"] = base["available"] and ready
    if not base["available"]:
        base["reason"] = "未检测到 NVIDIA GPU（驱动或设备）"
        base["name"] = "CPU（未检测到 GPU）"
    elif not ready:
        base["reason"] = "显卡识别所需的 CUDA/cuDNN 运行库不完整"
        base["name"] = "CPU（CUDA 运行库不可用）"
    return base


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


def _cache_dir() -> str:
    d = os.path.join(get_config().dirs.speech_models, ".hf_cache")
    os.makedirs(d, exist_ok=True)
    return d


def models_root() -> str:
    """模型实际落盘目录（faster-whisper 的 download_root / 手动导入目标）。

    必须让「下载/导入的写入目录」与「is_model_downloaded 的检测目录」是同一个：
    历史 bug 是检测写在 _cache_dir() 根，而下载/导入写在 _cache_dir()/models，
    导致模型明明已下载却一直被判定为「未下载」。
    """
    d = os.path.join(_cache_dir(), "models")
    os.makedirs(d, exist_ok=True)
    return d


def is_model_downloaded(size: str) -> bool:
    """判断某档 whisper 模型是否已下载。

    同时兼容历史遗留布局（旧版本曾写到 _cache_dir() 根），避免老用户升级后
    明明已下载却被要求重新下载。

    关键修复：snapshot 目录里只是指向 blobs/ 的 symlink / hardlink；必须验证
    链接**真实可解析到有内容的文件**，否则会出现「下载了 1MB 就以为 472MB
    已就绪」的假阳性 —— 之前用户的 small 模型就是 snapshot 在但 blobs 空，
    这里返回 True，实际加载时 ct2 报「Unable to open file model.bin」。
    """
    for root in (models_root(), _cache_dir()):
        for name in _repo_cache_names(size):
            if _valid_model_tree(os.path.join(root, name)):
                return True
    return False


def validate_model_installation(size: str) -> dict:
    """找到并校验实际会加载的本地快照；兼容旧目录和非 main 快照。"""
    first_error = None
    for root in (models_root(), _cache_dir()):
        for name in _repo_cache_names(size):
            repo_dir = os.path.join(root, name)
            if not os.path.isdir(repo_dir):
                continue
            snapshots = os.path.join(repo_dir, "snapshots")
            revisions = []
            if os.path.isdir(snapshots):
                revisions = [os.path.join(snapshots, d) for d in os.listdir(snapshots)
                             if os.path.isdir(os.path.join(snapshots, d))]
                revisions.sort(key=os.path.getmtime, reverse=True)
                try:
                    with open(os.path.join(repo_dir, "refs", "main"), encoding="utf-8") as f:
                        main_ref = f.read().strip()
                    preferred = os.path.join(snapshots, main_ref)
                    if preferred in revisions:
                        revisions.remove(preferred)
                        revisions.insert(0, preferred)
                except OSError:
                    pass
            if os.path.isfile(os.path.join(repo_dir, "model.bin")):
                revisions.append(repo_dir)
            for model_path in revisions:
                result = _validate_model_snapshot(size, repo_dir, model_path)
                if result["ok"]:
                    return result
                if first_error is None:
                    first_error = result
    return first_error or {"ok": False, "reason": "no_snapshot", "missing_files": [],
                           "repo_dir": "", "model_path": "", "size": size,
                           "detail": "未找到完整的本地模型快照，请在设置中下载语音模型"}


def _validate_model_snapshot(size: str, repo_dir: str, rev_dir: str) -> dict:
    """严格校验某档 whisper 模型是否真的可用。

    检查项：
    - snapshot 目录存在且至少一个 revision
    - snapshot 里 model.bin / config.json / tokenizer.json / vocabulary.txt 全部
      能被 `os.path.exists` + `os.path.getsize` 解析为非空文件（symlink / hardlink
      跟随到 blobs 里也要真的有内容）
    - 任意 blob 是断链（dangling symlink）或体积 < 10 MiB → 整体判定 broken

    返回：
    - ok: bool
    - reason: "" / "blob_missing" / "blob_truncated" / "snapshot_empty" / "no_snapshot"
    - missing_files: list[str]
    - repo_dir: 实际命中的 repo 路径
    - detail: 给前端展示的人话
    """
    out = {"ok": False, "reason": "", "missing_files": [], "repo_dir": repo_dir,
           "model_path": rev_dir, "size": size, "detail": ""}

    # 校验关键文件
    required = ["model.bin", "config.json", "tokenizer.json"]
    required_min = {"model.bin": _MIN_MODEL_BIN_BYTES}
    for fn in required:
        p = os.path.join(rev_dir, fn)
        if not os.path.exists(p):
            out["missing_files"].append(fn)
            continue
        # Windows 上 snapshot 文件可能是 symlink，要跟随
        try:
            real = os.path.realpath(p)
            if real != p and not os.path.exists(real):
                out["missing_files"].append(fn)
                continue
            sz = os.path.getsize(real)
        except OSError:
            out["missing_files"].append(fn)
            continue
        min_sz = required_min.get(fn, 1)
        if sz < min_sz:
            out["missing_files"].append(f"{fn} (size={sz} < {min_sz})")

    # Current turbo snapshots use vocabulary.json; older Systran snapshots can
    # use vocabulary.txt.  Either is a complete CTranslate2 vocabulary.
    vocab_paths = [os.path.join(rev_dir, "vocabulary.json"),
                   os.path.join(rev_dir, "vocabulary.txt")]
    if not any(os.path.isfile(p) and os.path.getsize(p) > 0 for p in vocab_paths):
        out["missing_files"].append("vocabulary.json/vocabulary.txt")

    if out["missing_files"]:
        out["reason"] = "blob_missing" if any(
            "(size=" not in m for m in out["missing_files"]
        ) else "blob_truncated"
        out["detail"] = (
            "模型文件缺失或不完整：" + ", ".join(out["missing_files"])
            + "。请到「设置 → 模型」点击「重新校验」后再点「下载语音模型」。"
        )
        return out

    out["ok"] = True
    out["detail"] = "模型文件完整可用"
    return out


def cleanup_broken_model(size: str) -> dict:
    """删掉 broken 的模型 repo，让用户能重新下载。"""
    import shutil
    removed = []
    for root in (models_root(), _cache_dir()):
        for name in _repo_cache_names(size):
            cand = os.path.join(root, name)
            if os.path.isdir(cand):
                try:
                    shutil.rmtree(cand, ignore_errors=True)
                    removed.append(cand)
                except Exception:  # noqa: BLE001
                    pass
    return {"size": size, "removed": removed, "now_downloaded": is_model_downloaded(size)}


def _valid_model_tree(repo_dir: str) -> bool:
    """确认缓存中至少存在一个结构完整、体积合理的模型快照。"""
    if not os.path.isdir(repo_dir):
        return False
    for base, dirs, files in os.walk(repo_dir):
        # HuggingFace 缓存通常是 snapshots/<revision>/；手动导入也允许把
        # CTranslate2 文件直接放在仓库目录里。
        if "model.bin" not in files or "config.json" not in files:
            continue
        if not ({"tokenizer.json", "vocabulary.json"} & set(files)):
            continue
        model_bin = os.path.join(base, "model.bin")
        try:
            if os.path.getsize(model_bin) >= _MIN_MODEL_BIN_BYTES:
                return True
        except OSError:
            pass
        # 不必继续深入一个已是快照的目录。
        dirs[:] = []
    return False


def _looks_like_cuda_runtime_error(exc: Exception) -> bool:
    msg = str(exc)
    if not msg:
        return False
    low = msg.lower()
    keys = ("cublas", "cudnn", "curand", "cublaslt", "cuda", "library ", "dll")
    return any(k in low for k in keys)


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
    if (requested or "auto") in {"auto", "mix"} and language not in {"en", "zh"}:
        raise ValueError(
            f"自动识别得到的语言（{language}）与中英文课堂不符，已暂停，避免生成错误笔记。"
            "请在「识别设置」中选择老师实际使用的语言，再点「重新转写」。原录音和旧结果仍保留。")


def transcribe(audio_path: str, size: str = "small", language: str | None = None,
               device_pref: str = "auto", progress_cb=None, compute_type: str | None = None,
               on_retry=None, vocabulary=""):
    """转写一段音频。

    新增 `device_pref` 与 `on_retry`：
    - device_pref: "auto" / "cpu" / "cuda"。auto 优先 GPU，不可用时 CPU。
    - on_retry(reason, exc): GPU 失败回退 CPU 时回调，便于前端提示
      "已自动切换到 CPU 重新识别"。
    """
    choice = resolve_device(device_pref)
    device = choice["device"]
    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    if choice["used_cuda"]:
        LOG.info("加载语音模型 size=%s device=cuda compute_type=float16（偏好=%s）",
                 size, device_pref)
    else:
        reason = choice.get("reason") or "无可用 GPU"
        LOG.info("加载语音模型 size=%s device=cpu compute_type=int8（GPU 不可用：%s；偏好=%s）",
                 size, reason, device_pref)

    cache = _cache_dir()
    os.environ.setdefault("HF_HOME", cache)
    validation = validate_model_installation(size)
    if not validation["ok"]:
        raise RuntimeError(validation["detail"])

    from faster_whisper import WhisperModel

    def _do_transcribe(dev: str, ct: str):
        model = WhisperModel(
            validation["model_path"], device=dev, compute_type=ct,
            download_root=models_root(), num_workers=1, local_files_only=True,
            cpu_threads=min(6, max(1, (os.cpu_count() or 2) - 1)),
        )
        try:
            segments_iter, sinfo = model.transcribe(
                audio_path,
                **recognition_options(language, vocabulary),
                beam_size=5,
                vad_filter=True,
                vad_parameters={"threshold": 0.5, "min_silence_duration_ms": 500, "speech_pad_ms": 200},
                condition_on_previous_text=False,
                word_timestamps=False,
                temperature=[0.0, 0.4, 0.8],
            )
            check_detected_language(sinfo.language, language)
            out = []
            for seg in segments_iter:
                if progress_cb:
                    progress_cb(seg.end)
                uncertain = segment_is_uncertain(seg)
                text = (seg.text or "").strip()
                if not text:
                    continue
                out.append({
                    "start_seconds": round(float(seg.start), 2),
                    "end_seconds": round(float(seg.end), 2),
                    "text": text,
                    "original_text": text,
                    "language": "mix" if language == "mix" else sinfo.language,
                    "confidence": None,
                    "is_uncertain": uncertain,
                })
        finally:
            del model
        return out, "mix" if language == "mix" else sinfo.language, dev

    try:
        out, lang, dev = _do_transcribe(device, compute_type)
    except Exception as e:  # noqa: BLE001
        # 关键兜底：即便探测说 GPU 可用，运行时仍可能因 dll 路径/版本不匹配而崩。
        # 自动改 CPU 重试一次，并让后续请求也不要再走 GPU（本次进程内）。
        if device == "cuda" and _looks_like_cuda_runtime_error(e):
            LOG.warning("GPU 转写失败，自动回退 CPU 重试一次：%s", e)
            global _cuda_ready
            with _cuda_probe_lock:
                _cuda_ready = False
            if on_retry is not None:
                try:
                    on_retry("cuda_failed_fallback_cpu", str(e))
                except Exception:  # noqa: BLE001
                    pass
            out, lang, dev = _do_transcribe("cpu", "int8")
        else:
            raise

    if not out:
        raise ValueError("这段录音中没有识别到清晰的人声。请先播放录音确认有声音，再尝试重新转写。")
    LOG.info("转写完成：%d 段，语言=%s，设备=%s", len(out), lang, dev)
    return out, lang, dev


def segment_is_uncertain(seg):
    """Conservative signal, not an assertion that unflagged text is correct."""
    return (float(getattr(seg, 'no_speech_prob', 0) or 0) > 0.5 or
            float(getattr(seg, 'avg_logprob', 0) or 0) < -0.8 or
            float(getattr(seg, 'compression_ratio', 0) or 0) > 2.4)
