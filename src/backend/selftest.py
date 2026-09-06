"""一键自检 / 就绪检查（对应需求文档阶段四 / 阶段九）。

两层：
- 基础自检 `--self-test`：应用目录、数据目录可写、数据库打开与迁移、前端产物存在、
  后端启动 + /api/health 200（含端口绑定）、WebView2 状态、日志目录可写、模型目录、
  模型状态（仅报告）、GPU 信息。不依赖真实模型与 GUI。致命项失败 → 退出码 2。
- 就绪检查 `--readiness-check`：在基础自检通过的前提下，对真实模型做
  文件 + sha256 + 真实加载 + 真实转写（语音）/ 真实结构化分析（文字）+ 5 层证据关 +
  落库 + 搜索命中；按退出码区分结果（0/10/11/12/13/20/21/22）。

Settings / 首启向导复用同一份「就绪状态」：`/api/readiness` 与 `--readiness-check`
都来自本模块的函数，不各自实现。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
import wave

from . import config, db
from .ai import llm_provider, manifest, whisper_worker
from .ai.provider import detect_status, provider_is_ready
from .logutil import get_logger

LOG = get_logger("selftest")

# 就绪检查退出码
EXIT_READY = 0
EXIT_BASE_OK_MODEL_MISSING = 10
EXIT_WHISPER_UNUSABLE = 11
EXIT_LLM_UNUSABLE = 12
EXIT_CPU_OK = 13
EXIT_DB_APP_ERROR = 20
EXIT_FRONTEND_ERROR = 21
EXIT_BACKEND_FAIL = 22


# ----------------------------------------------------------------------------
# 基础自检
# ----------------------------------------------------------------------------
def _check(name, fn, fatal=True):
    try:
        ok, detail = fn()
        return {"name": name, "ok": bool(ok), "fatal": fatal, "detail": detail or ""}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "ok": False, "fatal": fatal, "detail": f"异常: {e}"}


def _detect_webview2() -> tuple[bool, str]:
    """检测 WebView2 运行时是否可用（Windows）。非 Windows 视为不需要。"""
    if sys.platform != "win32":
        return True, "非 Windows 平台（无需 WebView2）"
    # 1) 文件系统：Evergreen 固定版本或系统版
    candidates = [
        r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
    ]
    for base in candidates:
        if os.path.isdir(base):
            for ver in os.listdir(base):
                exe = os.path.join(base, ver, "msedgewebview2.exe")
                if os.path.isfile(exe):
                    return True, f"WebView2 运行时存在: {exe}"
    # 2) 注册表兜底（HKCU / HKLM）
    try:
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\EdgeWebView\WebView2\Core")
                path = winreg.QueryValueEx(k, "EBWebView")[0]
                if isinstance(path, str) and os.path.isdir(path):
                    return True, f"WebView2 运行时（注册表）: {path}"
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return False, "未检测到 WebView2 运行时（应用可启动但窗口将无法显示，需安装 Evergreen 运行时）"


def run_self_test(fatal_on_missing_static: bool = False) -> dict:
    cfg = config.init_config()
    checks: list[dict] = []

    # 0. 应用目录（用可执行文件所在目录判断，PyInstaller 打包后 __file__ 指向冻结的 _internal，不可靠）
    def app_dir():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.isdir(exe_dir), f"应用目录: {exe_dir}"
    checks.append(_check("应用目录", app_dir))

    # 1. 数据目录可写
    def data_dir_writable():
        d = cfg.dirs.data_dir
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".selftest_probe")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return True, f"可写: {d}"
        except Exception as e:  # noqa: BLE001
            return False, f"不可写: {e}"
    checks.append(_check("数据目录可写", data_dir_writable))

    # 2. 数据库打开与迁移
    def db_migrate():
        db.init_db()
        n = db.read_conn().execute("SELECT COUNT(*) FROM important_items").fetchone()[0]
        return True, f"迁移成功（important_items 现有 {n} 行）"
    checks.append(_check("数据库迁移", db_migrate))

    # 3. 日志目录可写
    def log_dir_writable():
        d = cfg.dirs.logs
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".selftest_probe")
        try:
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return True, f"可写: {d}"
        except Exception as e:  # noqa: BLE001
            return False, f"不可写: {e}"
    checks.append(_check("日志目录可写", log_dir_writable))

    # 4. 模型目录可创建
    def model_dirs():
        for d in (cfg.dirs.speech_models, cfg.dirs.llm_models):
            os.makedirs(d, exist_ok=True)
            if not os.path.isdir(d):
                return False, f"无法创建模型目录: {d}"
        return True, f"语音模型: {cfg.dirs.speech_models}；文字模型: {cfg.dirs.llm_models}"
    checks.append(_check("模型目录", model_dirs))

    # 5. 后端启动 + 健康检查（端口绑定 + 连通性）
    from .api import DIST_DIR, make_server

    server = None

    def backend_up():
        nonlocal server
        try:
            server = make_server("127.0.0.1", 0)
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                data = json.loads(r.read())
                return bool(data.get("ok")), f"健康检查 200 @ 127.0.0.1:{port}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)
    checks.append(_check("后端启动+健康检查", backend_up))

    # 6. 前端产物存在
    def static_exists():
        idx = os.path.join(DIST_DIR, "index.html")  # DIST_DIR 已在上方导入
        if os.path.isfile(idx):
            return True, f"前端产物存在: {DIST_DIR}"
        return (not fatal_on_missing_static), f"未找到前端产物（开发模式可忽略）: {DIST_DIR}"
    checks.append(_check("前端产物", static_exists, fatal=fatal_on_missing_static))

    # 7. WebView2 状态（仅报告，缺失由首启向导引导安装，不计入致命）
    def webview2():
        ok, detail = _detect_webview2()
        return True, detail  # 不致命：后端仍可用，GUI 缺失走引导
    checks.append(_check("WebView2 状态", webview2, fatal=False))

    # 8. 模型状态（非致命，仅报告）
    def model_status():
        ws = whisper_worker.is_model_downloaded(
            manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"])
        settings = db.get_all_settings()
        st = detect_status(settings)
        llm_dl = provider_is_ready(settings, st)
        return True, f"whisper 已下载={ws}; llm 已下载={llm_dl}"
    checks.append(_check("模型状态", model_status, fatal=False))

    # 9. GPU 信息（非致命，仅报告）
    def gpu_info():
        return True, f"GPU: {whisper_worker.gpu_info()}"
    checks.append(_check("GPU信息", gpu_info, fatal=False))

    fatal = [c for c in checks if (not c["ok"] and c["fatal"])]
    if server:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass
    db.close_all_connections()
    return {"checks": checks, "ok": len(fatal) == 0, "fatal_count": len(fatal)}


def compute_readiness_state() -> dict:
    """轻量就绪状态（不启动额外后端、不下载模型）。

    供前端「首启向导 / 设置」复用同一份状态，避免各自实现导致口径不一致。
    """
    cfg = config.init_config()
    from .api import DIST_DIR

    settings = db.get_all_settings()
    tier = settings.get("whisper_tier") or manifest.DEFAULT_WHISPER_TIER
    whisper_size = manifest.WHISPER_TIERS.get(
        tier, manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]
    )["model_size"]
    ws = whisper_worker.is_model_downloaded(whisper_size)
    st = detect_status(settings)
    llm_dl = provider_is_ready(settings, st)
    current_llm = manifest.llm_model_by_id(settings.get("llm_model_id") or "") or manifest.default_llm_model()
    wv_ok, wv_detail = _detect_webview2()
    return {
        "whisper_downloaded": ws,
        "whisper_model_size": whisper_size,
        "llm_downloaded": llm_dl,
        "llm_model_label": current_llm["display"],
        "webview2": {"ok": wv_ok, "detail": wv_detail},
        "gpu": whisper_worker.gpu_info(),
        "frontend_present": os.path.isfile(os.path.join(DIST_DIR, "index.html")),
        "data_dir": cfg.dirs.data_dir,
    }


# ----------------------------------------------------------------------------
# 就绪检查（真实模型）
# ----------------------------------------------------------------------------
def _sha256_of(path: str, limit: int = 0) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                if limit and f.tell() >= limit:
                    break
    except Exception:  # noqa: BLE001
        return None
    return h.hexdigest()


def _make_tone_sample(path: str, seconds: float = 3.0, hz: float = 220.0) -> str:
    """生成一段纯音（非语音）样例，用于验证 VAD/静音不过度虚构。返回路径。"""
    n = int(16000 * seconds)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        import math
        import struct

        for i in range(n):
            v = int(8000 * math.sin(2 * math.pi * hz * i / 16000))
            w.writeframes(struct.pack("<h", v))
    return path


def _check_whisper_ready(tmp: str, size: str | None = None) -> dict:
    """真实加载 + 真实转写一段样例音频。返回 {ready, detail, file, sha256, loaded, transcribed, segments}。"""
    if not size:
        settings = db.get_all_settings()
        tier = settings.get("whisper_tier") or manifest.DEFAULT_WHISPER_TIER
        size = manifest.WHISPER_TIERS.get(
            tier, manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]
        )["model_size"]
    res = {"ready": False, "file": None, "sha256": None, "loaded": False,
           "transcribed": False, "segments": 0, "detail": ""}
    if not whisper_worker.is_model_downloaded(size):
        res["detail"] = "语音模型未下载"
        return res
    # 模型文件 sha256（只取缓存目录主模型文件）
    try:
        import glob
        cands = []
        for cache_name in whisper_worker._repo_cache_names(size):
            cands.extend(glob.glob(os.path.join(
                whisper_worker.models_root(), cache_name, "**", "*.bin"),
                recursive=True))
        cands.sort()
        if cands:
            res["file"] = cands[0]
            res["sha256"] = _sha256_of(cands[0], limit=1 << 26)
    except Exception:  # noqa: BLE001
        pass
    # 真实加载
    try:
        from faster_whisper import WhisperModel

        # 设备选择交给 whisper_worker 统一处理：用户偏好 → 实际可用。
        # 之前这里写死 info["available"] → cuda 是 PyInstaller 打包后 fail 的根源。
        settings = db.get_all_settings()
        pref = (settings.get("whisper_device") or "auto").lower()
        plan = whisper_worker.resolve_device(pref)
        device = plan["device"]
        compute_type = "float16" if device == "cuda" else "int8"
        os.environ.setdefault("HF_HOME", whisper_worker._cache_dir())
        model = WhisperModel(size, device=device, compute_type=compute_type,
                             download_root=whisper_worker.models_root(),
                             num_workers=2)
        res["loaded"] = True
    except Exception as e:  # noqa: BLE001
        res["detail"] = f"语音模型加载失败: {e}"
        return res
    # 真实转写（纯音样例：验证 VAD 不过度虚构）
    sample = os.path.join(tmp, "readiness_tone.wav")
    _make_tone_sample(sample, seconds=3.0)
    try:
        segs, _lang, _device = whisper_worker.transcribe(sample, size=size, language=None)
        res["transcribed"] = True
        res["segments"] = len(segs)
        total_chars = sum(len(s.get("text", "")) for s in segs)
        # 纯音不应产生大量虚构文字
        if total_chars > 200:
            res["detail"] = f"转写产出异常多文字（{total_chars} 字），疑似静音虚构"
            return res
        res["ready"] = True
        res["detail"] = f"加载成功（{device}/{compute_type}）；转写 {len(segs)} 段，{total_chars} 字（静音未虚构）"
    except Exception as e:  # noqa: BLE001
        # 纯音被 VAD 正确过滤时，正式转写入口会用这条提示结束；这恰好证明
        # 模型已完成加载和推理，而不是测试失败。
        if "没有识别到清晰的人声" in str(e):
            res["transcribed"] = True
            res["ready"] = True
            res["detail"] = f"加载与推理成功（{device}/{compute_type}）；测试纯音未产生虚构文字"
        else:
            res["detail"] = f"语音转写失败: {e}"
    return res


def _check_llm_ready(tmp: str) -> dict:
    """真实加载 sidecar + 真实结构化分析验收样例 + 5 层关 + 落库 + 搜索命中。

    落库使用一次性 semester→course→session（清理时按级联删除），不污染用户真实数据。
    """
    res = {"ready": False, "file": None, "sha256": None, "loaded": False,
           "analyzed": False, "saved": False, "searched": False, "detail": ""}
    sem_id = course_id = sess_id = None
    model_id = manifest.default_llm_model()["id"]
    try:
        side = llm_provider.LlamaCppSidecarLLM(model_id)
    except Exception as e:  # noqa: BLE001
        res["detail"] = f"无法构建 sidecar 提供方: {e}"
        return res
    if not side.available():
        res["detail"] = "文字模型或 sidecar 二进制未下载"
        return res
    # 文件 + sha256
    try:
        res["file"] = side._bin_path() if hasattr(side, "_bin_path") else None
        mpath = side.model.get("path") if isinstance(getattr(side, "model", None), dict) else None
        if mpath and os.path.isfile(mpath):
            res["sha256"] = _sha256_of(mpath, limit=1 << 26)
    except Exception:  # noqa: BLE001
        pass
    # 真实加载
    try:
        side.start()
        res["loaded"] = True
    except Exception as e:  # noqa: BLE001
        res["detail"] = f"sidecar 启动失败: {e}"
        return res
    try:
        from .ai import segment_analysis, verification

        ACCEPTANCE = [
            "今天我们主要介绍消费者购买决策。",
            "期中考试安排在十月二十日，范围是第一至第四周。",
            "第二次作业不用提交。",
            "下周课程换到H302。",
            "星期五有一场投资讲座，自愿参加。",
        ]
        t = 0.0
        segs = []
        for txt in ACCEPTANCE:
            dur = max(2.0, len(txt) * 0.25)
            segs.append({"start_seconds": round(t, 1), "end_seconds": round(t + dur, 1),
                         "text": txt, "language": "zh", "is_uncertain": False})
            t += dur
        meta = {"session_date": "2026-09-09", "course_name": "市场营销", "language_mode": "auto"}
        _, items = segment_analysis.analyze_session(side, segs, meta)
        res["analyzed"] = True
        # 5 层证据关
        vres = verification.apply_verification(items, segs, {"audio_duration_seconds": t})
        confirmed = [i for i in vres["confirmed"]]
        # 一次性会话链（级联清理）
        sem_id = db.gen_id("sem")
        course_id = db.gen_id("course")
        sess_id = db.gen_id("sess")
        now = db.now_iso()
        with db.write_txn() as c:
            c.execute("INSERT INTO semesters(id,name,start_date,end_date,timezone,is_active,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (sem_id, " readiness", "2026-09-01", "2027-01-31", "Asia/Hong_Kong", 0, now, now))
            c.execute("INSERT INTO courses(id,semester_id,name,code,language_mode,color,is_archived,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?)",
                      (course_id, sem_id, "市场营销", "MKT", "auto", "#4CAF50", 0, now, now))
            c.execute("INSERT INTO class_sessions(id,course_id,session_date,status,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?)",
                      (sess_id, course_id, "2026-09-09", "done", now, now))
        all_items = [{**i, "course_id": course_id} for i in (vres["confirmed"] + vres["pending"] + vres["rejected"])]
        db.replace_items(sess_id, all_items)
        db.index_session(sess_id)
        res["saved"] = True
        if confirmed:
            q = confirmed[0]["title"][:4]
            res["searched"] = bool(db.search(q))
            res["ready"] = True
            res["detail"] = (f"加载成功；分析出 {len(items)} 条候选，{len(confirmed)} 条确认；"
                             f"落库=是；搜索命中={'是' if res['searched'] else '否'}")
        else:
            res["detail"] = "真实分析未产出任何确认事项（检查提示词/模型）"
    except Exception as e:  # noqa: BLE001
        res["detail"] = f"结构化分析失败: {e}"
    finally:
        try:
            side.stop()
        except Exception:  # noqa: BLE001
            pass
        # 级联清理（删 semester 即删 course/session/item）
        if sem_id:
            try:
                with db.write_txn() as c:
                    c.execute("DELETE FROM semesters WHERE id=?", (sem_id,))
            except Exception:  # noqa: BLE001
                pass
    return res


def run_readiness_check(fatal_on_missing_static: bool = False) -> dict:
    """完整就绪检查：基础自检 + 真实模型加载/转写/分析。

    返回 {base, whisper, llm, gpu, webview2, overall, exit_code}。
    """
    import tempfile

    base = run_self_test(fatal_on_missing_static=fatal_on_missing_static)

    # 基础致命项 → 直接给应用/后端错误码
    if not base["ok"]:
        # 区分前端缺失（打包后）与后端/DB 失败
        names = {c["name"]: c for c in base["checks"]}
        if not names.get("前端产物", {}).get("ok", True) and fatal_on_missing_static:
            return _finalize(base, EXIT_FRONTEND_ERROR, "前端产物缺失")
        if not names.get("后端启动+健康检查", {}).get("ok", True):
            return _finalize(base, EXIT_BACKEND_FAIL, "后端无法启动")
        return _finalize(base, EXIT_DB_APP_ERROR, "数据库/应用核心检查失败")

    webview2 = _detect_webview2()
    gpu = whisper_worker.gpu_info()

    tmp = tempfile.mkdtemp(prefix="ca_readiness_")
    wres = _check_whisper_ready(tmp)
    lres = _check_llm_ready(tmp)

    whisper_missing = not wres["ready"] and "未下载" in wres["detail"]
    llm_missing = not lres["ready"] and ("未下载" in lres["detail"] or "二进制" in lres["detail"])

    # 计算退出码
    if not wres["ready"] and not lres["ready"]:
        if whisper_missing and llm_missing:
            return _finalize(base, EXIT_BASE_OK_MODEL_MISSING,
                             "应用可以启动，但尚未具备处理课堂的条件。缺少：语音识别模型、文字分析模型。",
                             wres, lres, gpu, webview2)
        return _finalize(base, EXIT_BASE_OK_MODEL_MISSING,
                         "应用可以启动，但部分模型缺失：语音/文字模型未完成加载。",
                         wres, lres, gpu, webview2)
    if wres.get("file") and not wres["ready"]:
        return _finalize(base, EXIT_WHISPER_UNUSABLE, f"语音模型不可用: {wres['detail']}",
                         wres, lres, gpu, webview2)
    if lres.get("file") and not lres["ready"]:
        return _finalize(base, EXIT_LLM_UNUSABLE, f"文字模型不可用: {lres['detail']}",
                         wres, lres, gpu, webview2)
    # 两者就绪
    if gpu["available"]:
        return _finalize(base, EXIT_READY, "全部就绪（GPU）", wres, lres, gpu, webview2)
    return _finalize(base, EXIT_CPU_OK, "就绪（CPU 模式，速度较慢但可用）", wres, lres, gpu, webview2)


def _finalize(base, exit_code, msg, wres=None, lres=None, gpu=None, webview2=None) -> dict:
    return {
        "base": base,
        "whisper": wres or {"ready": False, "detail": ""},
        "llm": lres or {"ready": False, "detail": ""},
        "gpu": gpu or {"available": False},
        "webview2": {"ok": webview2[0], "detail": webview2[1]} if webview2 else {"ok": False},
        "overall": msg,
        "exit_code": exit_code,
    }


# ----------------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------------
def format_report(report: dict) -> str:
    lines = ["课堂助手 自检报告", "=" * 40]
    for c in report["checks"]:
        mark = "✅" if c["ok"] else ("❌" if c.get("fatal", True) else "⚠️")
        lines.append(f"  {mark} {c['name']}: {c['detail']}")
    lines.append("=" * 40)
    lines.append("结果: " + ("全部通过" if report["ok"] else f"存在 {report['fatal_count']} 项致命问题"))
    return "\n".join(lines)


def format_readiness_report(report: dict) -> str:
    base = report.get("base", {})
    lines = ["课堂助手 就绪检查", "=" * 40]
    for c in base.get("checks", []):
        mark = "✅" if c["ok"] else ("❌" if c.get("fatal", True) else "⚠️")
        lines.append(f"  {mark} {c['name']}: {c['detail']}")
    lines.append("-" * 40)
    w = report.get("whisper", {})
    l = report.get("llm", {})
    lines.append(f"  语音模型: {'✅ ' if w.get('ready') else '❌ '}{w.get('detail', '')}")
    lines.append(f"  文字模型: {'✅ ' if l.get('ready') else '❌ '}{l.get('detail', '')}")
    g = report.get("gpu", {})
    lines.append(f"  后端: {'GPU' if g.get('available') else 'CPU'}")
    v = report.get("webview2", {})
    lines.append(f"  WebView2: {'✅ ' if v.get('ok') else '⚠️ '}{v.get('detail', '')}")
    lines.append("=" * 40)
    lines.append(f"结论: {report.get('overall', '')}")
    lines.append(f"退出码: {report.get('exit_code', EXIT_DB_APP_ERROR)}")
    return "\n".join(lines)
