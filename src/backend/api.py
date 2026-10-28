"""HTTP API 与静态资源服务。

- 所有接口返回 JSON（/api/...），前端由本地后端同源提供，无需跨域。
- 音频/备份上传使用裸字节请求体（头部带 X-Session-Id / X-Filename），避免手写 multipart 解析。
- 处理任务在后台线程执行，API 立即返回 job_id，前端轮询 /api/jobs/<id> 获取进度。
- 同一时间只允许一个活动任务（active_jobs 约束）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import shutil
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import backup, config, dateparse, db, diagnostics, hashutil, jobs, paths, schedule, note_editing
from .ai import llm_provider, manifest, whisper_worker
from .selftest import compute_readiness_state
from .ai.provider import build_provider, detect_status, provider_is_ready
from .logutil import get_logger
from .pipeline import run_pipeline, _resolve_whisper_size

LOG = get_logger("api")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_dist_dir() -> str:
    """解析前端构建产物目录，兼容开发模式与 PyInstaller 打包后的两种形态。

    - 开发模式：BASE_DIR/frontend/dist
    - 单文件夹 exe：<可执行文件目录>/frontend/dist
    - 单文件 exe：MEIPASS 临时解压根/frontend/dist
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    cand = os.path.join(exe_dir, "frontend", "dist")
    if os.path.isdir(cand):
        return cand
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", exe_dir)
        cand2 = os.path.join(base, "frontend", "dist")
        if os.path.isdir(cand2):
            return cand2
    return os.path.join(BASE_DIR, "frontend", "dist")


DIST_DIR = _resolve_dist_dir()

MODEL_PROGRESS: dict = {"whisper": {}, "llm": {}}

# ---------------------------------------------------------------------------
# 后台任务
# ---------------------------------------------------------------------------
def _run_pipeline_thread(session_id, mode, whisper_tier, language, job_id):
    try:
        run_pipeline(session_id, mode=mode, whisper_tier=whisper_tier,
                     language=language, job_id=job_id)
    except Exception:  # noqa: BLE001
        LOG.exception("pipeline 线程异常")


def start_pipeline(session_id, mode, whisper_tier=None, language=None):
    with db.write_txn():
        if db.active_jobs():
            return None, "已有任务在进行中，请等待其完成或取消后再试"
        if not db.get_session(session_id):
            return None, '课堂记录不存在'
        job_type = "transcribe" if mode in ("transcribe", "retranscribe") else "analyze"
        job_id = db.create_job(session_id, job_type, status="pending", stage="排队中")
        db.update_session(session_id, status="file_check" if job_type == "transcribe" else "analyzing",
                          processing_error=None)
        jobs.clear_cancel(session_id)
    t = threading.Thread(target=_run_pipeline_thread, args=(session_id, mode, whisper_tier, language, job_id), daemon=True)
    t.start()
    return job_id, None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _send_json(handler, obj, status=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _send_text(handler, text, status=200):
    data = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_body(handler):
    length = int(handler.headers.get("Content-Length", 0) or 0)
    return handler.rfile.read(length) if length else b""


# 上传单文件最大 2 GiB。一次性读进内存对 100MB+ 的录音足够，但 4K 课堂
# 录像 / 超长录音的极端场景会被 OOM，所以这里统一走流式落盘。
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
_MAX_BATCH_FILES = 64
_CHUNK = 1024 * 1024


class _UploadTooLarge(Exception):
    pass


def _stream_to_file(handler, dest: str) -> tuple[int, str]:
    """把请求体流式写入 dest，并边写边算 SHA-256。返回 (字节数, hex sha256)。"""
    import hashlib
    sha = hashlib.sha256()
    written = 0
    length = handler.headers.get("Content-Length")
    if length:
        try:
            declared = int(length)
        except ValueError:
            declared = 0
        if declared > _MAX_UPLOAD_BYTES:
            raise _UploadTooLarge(f"文件过大：{declared} 字节（上限 {_MAX_UPLOAD_BYTES}）")
        remaining = declared
        with open(dest, "wb") as f:
            while remaining > 0:
                buf = handler.rfile.read(min(_CHUNK, remaining))
                if not buf:
                    break
                f.write(buf)
                sha.update(buf)
                written += len(buf)
                remaining -= len(buf)
        return written, sha.hexdigest()

    # chunked / 无 Content-Length
    with open(dest, "wb") as f:
        while True:
            buf = handler.rfile.read(_CHUNK)
            if not buf:
                break
            written += len(buf)
            if written > _MAX_UPLOAD_BYTES:
                f.close()
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise _UploadTooLarge(f"文件过大：超过 {_MAX_UPLOAD_BYTES} 字节")
            f.write(buf)
            sha.update(buf)
    return written, sha.hexdigest()


def _audio_collection_hash(files: list[dict]) -> str:
    """Stable hash for an ordered collection of uploaded audio files."""
    if len(files) == 1 and files[0].get("hash"):
        return files[0]["hash"]
    payload = json.dumps([f.get("hash") for f in files], separators=(",", ":"))
    return hashutil.sha256_bytes(payload.encode("utf-8"))


def _read_json(handler):
    raw = _read_body(handler)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _ts_parse(text):
    """从一行文本解析时间戳，支持 [mm:ss]/[hh:mm:ss]/[mm:ss.s]/(s-s)/[s] 等。"""
    pats = [
        r"\[(\d{1,2}):(\d{2})(?:\.(\d+))?\]",
        r"\[(\d{1,2}):(\d{2}):(\d{2})\]",
        r"\[(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\]",
    ]
    for p in pats:
        m = re.search(p, text)
        if m:
            g = m.groups()
            if len(g) == 3 and ":" in text and text.count(":") >= 1 and g[2] is not None and g[1] is not None and re.match(r"\d+:\d+:\d+", m.group(0)):
                h, mi, s = int(g[0]), int(g[1]), int(g[2]) if g[2].isdigit() else 0
                return float(h * 3600 + mi * 60 + s)
            if len(g) == 3 and g[2] is not None:
                # mm:ss(.fff)
                return float(int(g[0]) * 60 + int(g[1]) + (float("0." + g[2]) if g[2] else 0))
            if len(g) == 2:
                try:
                    return float(g[0]) if g[0] else None
                except ValueError:
                    return None
    return None


def parse_transcript_text(text: str):
    """解析粘贴/导入的文字为片段列表。带时间戳则保留；否则按段落，时间留空。"""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    segs = []
    i = 0
    buf = []
    cur_start = None
    for line in lines:
        ts = _ts_parse(line)
        # 若该行以时间戳开头，则作为新片段起点
        if ts is not None and re.match(r"^\[", line.strip()):
            if buf:
                segs.append({"start_seconds": cur_start, "end_seconds": None,
                             "text": " ".join(buf).strip(), "language": None, "is_uncertain": False})
                buf = []
            content = re.sub(r"^\[\S+\]\s*", "", line.strip())
            buf = [content] if content else []
            cur_start = ts
        else:
            buf.append(line.strip())
    if buf:
        segs.append({"start_seconds": cur_start, "end_seconds": None,
                     "text": " ".join(buf).strip(), "language": None, "is_uncertain": False})
    return segs


# ---------------------------------------------------------------------------
# 路由处理
# ---------------------------------------------------------------------------
class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默默认访问日志
        pass

    def _dispatch(self):
        try:
            p = self.path.split("?")[0]
            method = self.command
            if p.startswith("/api/"):
                self._handle_api(p, method)
            else:
                self._serve_static(p)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as e:
            # WebView2 may cancel an audio download or navigate away while the
            # response is still streaming. This is a client-side disconnect,
            # not an application failure; do not turn it into a misleading 500.
            LOG.info("客户端中断请求: %s (%s)", self.path, e)
        except Exception as e:  # noqa: BLE001
            LOG.exception("请求处理异常: %s", self.path)
            try:
                _send_json(self, {"error": str(e)}, status=500)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                LOG.info("返回错误响应时客户端已断开: %s", self.path)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    # ---------------- API ----------------
    def _handle_api(self, p, method):
        # 健康检查
        if p == "/api/health":
            return _send_json(self, {"ok": True})

        # 全局状态（前端首屏）
        if p == "/api/state" and method == "GET":
            return self._state()

        if p == "/api/dashboard" and method == "GET":
            sem = db.get_active_semester()
            return _send_json(self, db.dashboard_overview(sem["id"]) if sem else {
                "recent_sessions": [], "items": [],
            })

        # 学期
        if p == "/api/semesters" and method == "GET":
            return _send_json(self, {"semesters": db.list_semesters()})
        if p == "/api/semesters" and method == "POST":
            b = _read_json(self)
            sid = db.create_semester(b["name"], b["start_date"], b["end_date"],
                                     b.get("timezone", "Asia/Hong_Kong"))
            if b.get("activate", True):
                db.set_active_semester(sid)
            return _send_json(self, {"id": sid})
        m = re.match(r"/api/semesters/([\w-]+)/activate$", p)
        if m and method == "POST":
            db.set_active_semester(m.group(1))
            return _send_json(self, {"ok": True})

        # 课程
        if p == "/api/courses" and method == "GET":
            sem = self._qs().get("semester_id")
            return _send_json(self, {"courses": db.list_courses(semester_id=sem[0] if sem else None)})
        if p == "/api/courses" and method == "POST":
            b = _read_json(self)
            cid = db.create_course(b["semester_id"], b["name"], b.get("code"),
                                   b.get("teacher_name"), b.get("location"),
                                   b.get("language_mode", "auto"), b.get("color", "#4CAF50"))
            if b.get("slots"):
                db.replace_slots(cid, b["slots"])
            return _send_json(self, {"id": cid})

        # 创建/获取某次课堂记录（按 课程+日期 去重）
        if p == "/api/sessions" and method == "POST":
            b = _read_json(self)
            course_id = b.get("course_id")
            session_date = b.get("session_date")
            if not course_id or not session_date:
                return _send_json(self, {
                    "error": "缺少必要字段 course_id / session_date",
                    "code": "bad_request",
                }, status=400)
            if not db.get_course(course_id):
                return _send_json(self, {"error": "课程不存在", "code": "bad_request"}, status=400)
            sid = db.get_or_create_session(
                course_id, session_date,
                start_time=b.get("start_time"), end_time=b.get("end_time"),
                week_number=b.get("week_number"), location=b.get("location"),
                title=b.get("title"), status="waiting_upload",
            )
            return _send_json(self, {"id": sid})
        m = re.match(r"/api/courses/([\w-]+)$", p)
        if m and method == "GET":
            return _send_json(self, {"course": db.get_course(m.group(1)) or {}, "slots": db.list_slots(m.group(1))})
        if m and method == "PUT":
            b = _read_json(self)
            if "language_mode" in b and b["language_mode"] not in {"auto", "zh", "en", "mix"}:
                return _send_json(self, {"error": "不支持的课程语言"}, status=400)
            if "vocabulary" in b and (not isinstance(b["vocabulary"], str) or len(b["vocabulary"]) > 800):
                return _send_json(self, {"error": "课程术语请限制在 800 字以内"}, status=400)
            db.update_course(m.group(1), **b)
            return _send_json(self, {"ok": True})
        if m and method == "DELETE":
            self._delete_course(m.group(1))
            return _send_json(self, {"ok": True})
        m = re.match(r"/api/courses/([\w-]+)/slots$", p)
        if m and method == "PUT":
            b = _read_json(self)
            db.replace_slots(m.group(1), b.get("slots", []))
            return _send_json(self, {"ok": True})
        m = re.match(r"/api/courses/([\w-]+)/sessions$", p)
        if m and method == "GET":
            return _send_json(self, {"sessions": db.list_sessions(m.group(1))})

        # 本周
        m = re.match(r"/api/week$", p)
        if m and method == "GET":
            qs = self._qs()
            center = qs.get("center", [None])[0]
            from datetime import date
            import datetime as _dt
            center_d = date.today()
            if center:
                try:
                    center_d = _dt.datetime.strptime(center, "%Y-%m-%d").date()
                except Exception:
                    pass
            sem = db.get_active_semester()
            if not sem:
                return _send_json(self, {"week": [], "semester": None})
            courses = db.list_courses(semester_id=sem["id"])
            plan = schedule.build_week_plan(sem, courses, center_d)
            return _send_json(self, {"week": plan, "semester": sem, "center": center_d.isoformat()})

        # 课堂详情
        m = re.match(r"/api/sessions/([\w-]+)/notes/chapters/(\d+)$", p)
        if m and method == 'PUT':
            try:
                note_editing.correct_chapter(m.group(1), int(m.group(2)), _read_json(self))
                return _send_json(self, {'ok': True})
            except ValueError as e:
                return _send_json(self, {'error': str(e)}, status=409)
        m = re.match(r"/api/sessions/([\w-]+)/revisions/([\w-]+)$", p)
        if m and method == "GET":
            revision = db.read_conn().execute(
                "SELECT payload FROM session_revisions WHERE session_id=? AND id=?", m.groups()).fetchone()
            return _send_json(self, json.loads(revision["payload"]) if revision else {"error": "版本不存在"},
                              status=200 if revision else 404)
        m = re.match(r"/api/sessions/([\w-]+)$", p)
        if m and method == "GET":
            return self._session_detail(m.group(1))
        if m and method == "PUT":
            body = _read_json(self)
            title = str(body.get("title") or "").strip()
            if not title:
                return _send_json(self, {"error": "标题不能为空"}, status=400)
            if not db.get_session(m.group(1)):
                return _send_json(self, {"error": "不存在"}, status=404)
            db.update_session(m.group(1), title=title)
            return _send_json(self, {"ok": True, "title": title})
        if m and method == "DELETE":
            self._delete_session(m.group(1))
            return _send_json(self, {"ok": True})

        # 该课堂最新任务（用于进度展示）
        m = re.match(r"/api/sessions/([\w-]+)/job$", p)
        if m and method == "GET":
            job = db.read_conn().execute(
                "SELECT * FROM processing_jobs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                (m.group(1),)).fetchone()
            return _send_json(self, {"job": dict(job) if job else None})

        # 上传录音（裸字节）
        m = re.match(r"/api/sessions/([\w-]+)/audio(?:/(\d+))?$", p)
        if m and method == "POST" and m.group(2) is None:
            return self._upload_audio(m.group(1))
        if m and method == "DELETE":
            if m.group(2) is None:
                return self._delete_session_audio(m.group(1))
            return self._delete_staged_audio_file(m.group(1), int(m.group(2)))
        if m and method == "GET":
            return self._serve_session_audio(m.group(1), int(m.group(2) or 0))

        # 导入文字
        m = re.match(r"/api/sessions/([\w-]+)/transcript$", p)
        if m and method == "POST":
            return self._import_transcript(m.group(1))

        # 启动处理（或重新处理）
        m = re.match(r"/api/sessions/([\w-]+)/process$", p)
        if m and method == "POST":
            b = _read_json(self)
            mode = (b.get("mode") or "transcribe").lower()
            need_whisper = ("transcrib" in mode) or ("full" in mode)
            need_llm = ("analy" in mode) or ("full" in mode) or mode in {"transcribe", "retranscribe"}
            if not need_whisper and not need_llm:  # 未知模式，保守要求两者
                need_whisper = need_llm = True
            settings = db.get_all_settings()
            # 缺模型时提前返回明确错误，避免创建会静默失败的幽灵任务（阶段十七）
            if need_whisper:
                size = _resolve_whisper_size(b.get("whisper_tier"), settings)
                v = whisper_worker.validate_model_installation(size)
                if not v.get("ok"):
                    return _send_json(self, {
                        "error": f"语音模型（{size}）{v.get('detail') or '缺失或不完整'}，请先到设置页处理。",
                        "code": "model_missing" if v.get("reason") in {"no_snapshot", "model_missing"} else "model_broken",
                        "validation": v,
                    }, status=409)
            if need_llm:
                st = detect_status(settings)
                if not provider_is_ready(settings, st):
                    return _send_json(self, {"error": "请先在设置页下载文字整理模型，再开始分析。"}, status=409)
            job_id, err = start_pipeline(m.group(1), b.get("mode", "transcribe"),
                                         b.get("whisper_tier"), b.get("language"))
            if err:
                return _send_json(self, {"error": err}, status=409)
            return _send_json(self, {"job_id": job_id})

        # 任务
        m = re.match(r"/api/jobs/([\w-]+)$", p)
        if m and method == "GET":
            return _send_json(self, {"job": db.get_job(m.group(1)) or {}})
        m = re.match(r"/api/jobs/([\w-]+)/cancel$", p)
        if m and method == "POST":
            job = db.get_job(m.group(1))
            if job and job['status'] in note_editing.ACTIVE:
                jobs.request_cancel(job["session_id"])
                db.update_job(m.group(1), stage="正在停止，当前计算结束后退出")
            return _send_json(self, {"ok": True})

        # 重要事项
        if p == "/api/important" and method == "GET":
            return _send_json(self, {"items": db.list_items(self._qs_dict())})

        # 搜索
        if p == "/api/search" and method == "GET":
            return self._search()

        # 设置
        if p == "/api/settings" and method == "GET":
            return self._settings_get()
        if p == "/api/settings" and method == "PUT":
            return self._settings_put()

        # 测试 whisper / analysis
        if p == "/api/settings/test-whisper" and method == "POST":
            return self._test_whisper()
        if p == "/api/settings/test-analysis" and method == "POST":
            return self._test_analysis()

        # 模型
        if p == "/api/models/status" and method == "GET":
            return _send_json(self, {"progress": MODEL_PROGRESS,
                                     "whisper": self._whisper_status(),
                                     "llm": detect_status(db.get_all_settings())})
        if p == "/api/readiness" and method == "GET":
            # 首启向导 / 设置页共用的轻量就绪状态（与 --readiness-check 同源）
            return _send_json(self, compute_readiness_state())
        if p == "/api/diagnostics/export" and method == "POST":
            # 阶段十九：导出诊断包（不含音频/转写/密钥/模型）
            try:
                path = diagnostics.export_diagnostics()
                with open(path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{os.path.basename(path)}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:  # noqa: BLE001
                return _send_json(self, {"error": str(e)}, status=500)
        if p == "/api/models/whisper/download" and method == "POST":
            return self._download_whisper()
        if p == "/api/models/whisper/validate" and method == "POST":
            return self._validate_whisper()
        if p == "/api/models/whisper/cleanup" and method == "POST":
            return self._cleanup_whisper()
        if p == "/api/models/llm/download" and method == "POST":
            return self._download_llm()
        if p == "/api/models/llm/delete" and method == "POST":
            return self._delete_llm()
        if p == "/api/models/import" and method == "POST":
            return self._import_model()

        # 备份恢复
        if p == "/api/backup" and method == "GET":
            try:
                if self._qs().get("download"):
                    path = backup.create_backup(include_audio=True)
                    with open(path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="{os.path.basename(path)}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                path = backup.create_backup(include_audio=True)
                return _send_json(self, {"path": path})
            except Exception as e:  # noqa: BLE001
                return _send_json(self, {"error": str(e)}, status=500)
        if p == "/api/restore" and method == "POST":
            return self._restore()

        # 重要事项编辑
        m = re.match(r"/api/items/([\w-]+)$", p)
        if m and method == "PUT":
            b = _read_json(self)
            db.update_item(m.group(1), **b)
            return _send_json(self, {"ok": True})
        if m and method == "DELETE":
            db.delete_item(m.group(1))
            return _send_json(self, {"ok": True})

        # 片段编辑
        m = re.match(r"/api/segments/([\w-]+)$", p)
        if m and method == "PUT":
            b = _read_json(self)
            try:
                note_editing.correct_segment(m.group(1), b)
                return _send_json(self, {"ok": True})
            except ValueError as e:
                return _send_json(self, {'error': str(e)}, status=409)

        # 打开数据文件夹
        if p == "/api/system/open-data-folder" and method == "POST":
            try:
                os.startfile(config.get_config().dirs.data_dir)
                return _send_json(self, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return _send_json(self, {"error": str(e)}, status=500)

        return _send_json(self, {"error": "未知接口 " + p}, status=404)

    # ---------------- 各接口实现 ----------------
    def _qs(self):
        from urllib.parse import parse_qs
        return parse_qs(self.path.split("?")[1]) if "?" in self.path else {}

    def _qs_dict(self):
        from urllib.parse import parse_qs
        d = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
        return {k: (v[0] if v else "") for k, v in d.items()}

    def _state(self):
        sem = db.get_active_semester()
        has_sem = sem is not None
        courses = db.list_courses(semester_id=sem["id"]) if has_sem else []
        if self._qs_dict().get("brief") == "1":
            return _send_json(self, {
                "has_semester": has_sem,
                "semester": sem,
                "course_count": len(courses),
            })
        from .ai import whisper_worker as ww
        s = db.get_all_settings()
        return _send_json(self, {
            "has_semester": has_sem,
            "semester": sem,
            "course_count": len(courses),
            "data_dir": config.get_config().dirs.data_dir,
            "disk": config.get_config().dirs.disk_usage(),
            # Keep dashboard/settings startup light; actual CUDA probing is
            # deferred to Test or the first recording.
            "gpu": ww.gpu_info(probe=False),
            "whisper_resolved": {"device": "cpu" if (s.get("whisper_device") or "auto") == "cpu" else "auto",
                                 "requested": s.get("whisper_device") or "auto",
                                 "used_cuda": None, "reason": "处理时检测"},
            "whisper_downloaded": ww.is_model_downloaded(manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"]),
            "active_jobs": db.active_jobs(),
        })

    def _session_detail(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "不存在"}, status=404)
        course = db.get_course(sess["course_id"]) or {}
        audio_files = [
            {k: f.get(k) for k in ("index", "name", "bytes", "duration")}
            for f in db.audio_files_for_session(sess)
        ]
        segments = db.list_segments(sid)
        topics = db.list_topics(sid)
        items = db.list_items({"course_id": sess["course_id"]})
        items = [i for i in items if i["session_id"] == sid]
        return _send_json(self, {
            "session": sess, "course": course,
            "audio_files": audio_files,
            "segments": segments, "topics": topics, "items": items,
            "notes": json.loads(sess["notes_data"]) if sess.get("notes_data") else None,
            "revisions": db.list_revisions(sid),
        })

    def _upload_audio(self, sid):
        if db.active_jobs():
            return _send_json(self, {"error": "有课堂正在处理，请完成或取消后再上传。"}, status=409)
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "不存在"}, status=404)

        defer_processing = (self.headers.get("X-Defer-Processing", "") or "").lower() in {"1", "true", "yes"}

        batch_header = (self.headers.get("X-Batch-Upload", "") or "").lower()
        is_batch = batch_header in {"1", "true", "yes"}
        batch_index = 0
        batch_count = 1
        batch_existing_count = None
        if is_batch:
            try:
                batch_index = int(self.headers.get("X-Batch-Index", "-1"))
                batch_count = int(self.headers.get("X-Batch-Count", "0"))
            except ValueError:
                return _send_json(self, {"error": "批量上传参数无效", "code": "bad_batch"}, status=400)
            if batch_count < 1 or batch_count > _MAX_BATCH_FILES or not 0 <= batch_index < batch_count:
                return _send_json(self, {
                    "error": f"批量上传最多支持 {_MAX_BATCH_FILES} 个文件",
                    "code": "bad_batch",
                }, status=400)
            current_files = db.audio_files_for_session(sess)
            # 批量上传按选择顺序串行提交；保留课堂原有录音，并拒绝跳号，
            # 避免多个批次混在同一节课里。新客户端会带上原有文件数，
            # 旧客户端没有该头部时则按当前清单推断以保持兼容。
            existing_header = self.headers.get("X-Batch-Existing-Count")
            if existing_header not in (None, ""):
                try:
                    batch_existing_count = int(existing_header)
                except ValueError:
                    return _send_json(self, {"error": "批量上传原有文件数无效", "code": "bad_batch"}, status=400)
                if batch_existing_count < 0 or batch_existing_count > len(current_files):
                    return _send_json(self, {"error": "课堂录音清单已发生变化，请重新选择全部录音", "code": "batch_out_of_order"}, status=409)
            else:
                batch_existing_count = len(current_files) if batch_index == 0 else max(0, len(current_files) - batch_index)
            if len(current_files) != batch_existing_count + batch_index:
                return _send_json(self, {
                    "error": "批量上传顺序已失效，请重新选择全部录音",
                    "code": "batch_out_of_order",
                }, status=409)

        filename = self.headers.get("X-Filename", "audio.m4a")
        original_filename = self.headers.get("X-Original-Filename")
        if original_filename:
            try:
                from urllib.parse import unquote
                filename = unquote(original_filename).replace("\r", "").replace("\n", "")[:255] or filename
            except Exception:  # noqa: BLE001
                pass
        ext = os.path.splitext(filename)[1].lower() or ".m4a"
        if ext not in hashutil.SUPPORTED_AUDIO_EXT:
            return _send_json(self, {"error": f"不支持的格式：{ext}"}, status=400)
        # 先看声明的 Content-Length：超限就别开始读 body，立刻 413，
        # 避免「客户端发 3GB 真实文件」白白耗内存。
        try:
            declared_len = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            declared_len = 0
        if declared_len > _MAX_UPLOAD_BYTES:
            return _send_json(self, {
                "error": f"文件过大：{declared_len} 字节（上限 {_MAX_UPLOAD_BYTES}）",
                "code": "too_large",
            }, status=413)
        settings = db.get_all_settings()
        if not defer_processing:
            # 立即处理前核实语音模型快照与文字模型都可用；单纯暂存录音不依赖模型。
            size = _resolve_whisper_size(settings.get("whisper_tier"), settings)
            v = whisper_worker.validate_model_installation(size)
            if not v.get("ok"):
                detail = v.get("detail") or "模型文件缺失或不完整"
                return _send_json(self, {
                    "error": f"语音模型（{size}）{detail}。请到「设置 → 模型」点击「重新校验 / 清理后重新下载」，或先暂存录音。",
                    "code": "model_missing" if v.get("reason") in {"no_snapshot", "model_missing"} else "model_broken",
                    "validation": v,
                }, status=409)
            provider_status = detect_status(settings)
            if not provider_is_ready(settings, provider_status):
                return _send_json(self, {
                    "error": "文字整理模型尚未就绪。请先到设置页完成模型准备，或选择先保存录音、稍后处理。",
                    "code": "llm_missing",
                }, status=409)
        # 复制到受管目录（流式落盘 + 边写边算 hash，避免 100MB+ 录音一次性进内存）
        sem = db.get_semester(db.get_course(sess["course_id"])["semester_id"])
        adir = config.get_config().dirs.audio_dir_for(sem["id"], sess["course_id"], sid)
        dest = os.path.join(adir, "original" + ext)
        if os.path.exists(dest):
            dest = os.path.join(adir, db.gen_id("recording") + ext)
        try:
            written, fhash = _stream_to_file(self, dest)
        except _UploadTooLarge as e:
            return _send_json(self, {"error": str(e), "code": "too_large"}, status=413)
        if not written:
            try:
                os.remove(dest)
            except OSError:
                pass
            return _send_json(self, {"error": "空文件"}, status=400)
        if hashutil.is_audio_corrupted(dest):
            os.remove(dest)
            return _send_json(self, {"error": "音频文件已损坏，无法解码"}, status=400)
        dur = hashutil.audio_duration_seconds(dest)
        entry = {"path": dest, "name": filename, "bytes": written,
                 "duration": dur, "hash": fhash}
        if is_batch:
            # current_files contains the original recordings plus the files
            # already uploaded in this batch. Never discard the originals when
            # a user adds more recordings to an existing lesson.
            entries = current_files + [entry]
            duration_values = [e.get("duration") for e in entries]
            total_duration = (sum(float(v) for v in duration_values)
                              if all(v is not None for v in duration_values) else None)
            collection_hash = _audio_collection_hash(entries)
            # 首段开始一个新的批次，但保留课堂已有的录音文件，避免意外删除。
            if batch_index == 0:
                db.snapshot_session(sid, "上传新录音前")
            db.update_session(
                sid, audio_path=entries[0]["path"], audio_hash=collection_hash,
                audio_duration_seconds=total_duration, audio_manifest=json.dumps(
                    entries, ensure_ascii=False),
                status="ready_to_process" if defer_processing else "waiting_model", source_type="audio",
            )
        else:
            # 重复检测
            entries = [entry]
            total_duration = dur
            collection_hash = fhash
            dup = db.read_conn().execute(
                "SELECT id, course_id, session_date FROM class_sessions WHERE audio_hash=? AND id!=?",
                (fhash, sid)).fetchone()
            db.snapshot_session(sid, "上传新录音前")
            db.update_session(sid, audio_path=dest, audio_hash=fhash,
                              audio_duration_seconds=dur, audio_manifest=None,
                              status="ready_to_process" if defer_processing else "waiting_model",
                              source_type="audio")

        # 批量模式只在最后一个文件到达后启动一次流水线。
        if is_batch and batch_index < batch_count - 1:
            return _send_json(self, {
                "batch": True, "index": batch_index, "count": batch_count,
                "uploaded": batch_index + 1, "duration": total_duration, "bytes": written,
            })

        if defer_processing:
            return _send_json(self, {
                "staged": True, "ready_to_process": True,
                "duration": total_duration, "bytes": written,
                "file_count": len(entries),
            })

        dup = db.read_conn().execute(
            "SELECT id, course_id, session_date FROM class_sessions WHERE audio_hash=? AND id!=?",
            (collection_hash, sid)).fetchone()
        if dup:
            LOG.info("检测到相同音频已存在于其它课堂: %s", dup["id"])
        job_id, err = start_pipeline(sid, "transcribe")
        if err:
            return _send_json(self, {"error": err, "dup_with": dup["id"] if dup else None}, status=409)
        return _send_json(self, {
            "job_id": job_id,
            "duration": total_duration,
            "bytes": written,
            "file_count": len(entries),
            "dup_with": dup["id"] if dup else None,
        })

    def _import_transcript(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "不存在"}, status=404)
        b = _read_json(self)
        text = b.get("text", "")
        if not text.strip():
            return _send_json(self, {"error": "文字为空"}, status=400)
        segs = parse_transcript_text(text)
        if not segs:
            return _send_json(self, {"error": "未能解析出任何内容"}, status=400)
        settings = db.get_all_settings()
        st = detect_status(settings)
        if not provider_is_ready(settings, st):
            return _send_json(self, {
                "error": "文字已解析，但文字分析模型尚未就绪。请先到「设置 → 模型」完成准备，再重新导入。",
                "code": "llm_missing",
            }, status=409)
        if db.active_jobs():
            return _send_json(self, {"error": "有课堂正在处理，请完成或取消后再导入。"}, status=409)
        db.snapshot_session(sid, "导入文字前")
        db.replace_segments(sid, segs)
        db.replace_topics(sid, [])
        db.replace_items(sid, [])
        db.update_session(sid, notes_data=None)
        db.index_session(sid)
        lang = b.get("language") or "zh"
        db.update_session(sid, transcript_language=lang, source_type="text", status="analyzing")
        job_id, err = start_pipeline(sid, "analyze", language=lang)
        if err:
            return _send_json(self, {"error": err}, status=409)
        return _send_json(self, {"job_id": job_id, "segment_count": len(segs)})

    def _search(self):
        qs = self._qs_dict()
        q = qs.get("q", "")
        if not q:
            return _send_json(self, {"results": []})
        filters = {}
        if qs.get("course_id"):
            filters["course_id"] = qs["course_id"]
        if qs.get("type"):
            filters["content_type"] = qs["type"]
        weeks = qs.get("weeks")
        if weeks:
            from datetime import date, timedelta
            end = date.today()
            start = end - timedelta(weeks=int(weeks))
            filters["session_date_from"] = start.isoformat()
            filters["session_date_to"] = end.isoformat()
        if qs.get("from"):
            filters["session_date_from"] = qs["from"]
        if qs.get("to"):
            filters["session_date_to"] = qs["to"]
        rows = db.fts_search(q, filters)
        # 补全课程名/日期
        out = []
        for r in rows:
            course = db.get_course(r["course_id"]) or {}
            out.append({
                "row_type": r["row_type"],
                "ref_id": r["ref_id"],
                "session_id": r["session_id"],
                "course_id": r["course_id"],
                "course_name": course.get("name"),
                "session_date": r["session_date"],
                "content_type": r["content_type"],
                "snippet": r["snippet_text"],
            })
        return _send_json(self, {"results": out})

    def _settings_get(self):
        s = db.get_all_settings()
        # 将旧版本保存的 fast/balanced/accurate 档位迁移为唯一受支持的 Qwen 语音模型。
        # 这样设置页、数据库和实际处理链路不会再出现“显示一个、运行另一个”的情况。
        if s.get("whisper_tier") != manifest.DEFAULT_WHISPER_TIER:
            s["whisper_tier"] = manifest.DEFAULT_WHISPER_TIER
            db.set_setting("whisper_tier", manifest.DEFAULT_WHISPER_TIER)
        default_llm_id = manifest.default_llm_model()["id"]
        if s.get("llm_model_id") != default_llm_id:
            s["llm_model_id"] = default_llm_id
            db.set_setting("llm_model_id", default_llm_id)
        # 不返回明文密钥
        s.pop("openai_api_key", None)
        return _send_json(self, {
            "settings": s,
            "whisper_tiers": manifest.WHISPER_TIERS,
            "llm_models": manifest.LLM_MODELS,
            "provider_status": detect_status(s),
            "whisper_device_options": [
                {"value": "auto", "label": "自动（推荐：有可用显卡就用，否则 CPU）"},
                {"value": "cuda", "label": "优先 NVIDIA GPU（不可用时自动回退 CPU）"},
                {"value": "cpu", "label": "仅 CPU（更慢但兼容性最好）"},
            ],
            "whisper_gpu": whisper_worker.gpu_info(probe=False),
            "whisper_resolved": {"device": "cpu" if (s.get("whisper_device") or "auto") == "cpu" else "auto",
                                 "requested": s.get("whisper_device") or "auto",
                                 "used_cuda": None, "reason": "处理时检测"},
        })

    def _settings_put(self):
        b = _read_json(self)
        allowed = {
            "ai_provider", "llm_model_id", "whisper_device", "ollama_model",
            "openai_base_url", "openai_model", "openai_api_key",
        }
        for k, v in b.items():
            if k not in allowed:
                continue
            if k == "openai_api_key":
                if v:
                    from .credential import set_credential
                    set_credential("openai_api_key", v)
                else:
                    from .credential import delete_credential
                    delete_credential("openai_api_key")
                db.set_setting("openai_api_key_set", "1" if v else "0")
                continue
            if k == "llm_model_id":
                # 当前产品只维护一条本地文字整理链路，旧版本选择自动迁移。
                v = manifest.default_llm_model()["id"]
            if isinstance(v, bool):
                db.set_setting(k, "1" if v else "0")
            elif isinstance(v, (int, float)):
                db.set_setting(k, str(v))
            else:
                db.set_setting(k, v)
        return _send_json(self, {"ok": True})

    def _test_whisper(self):
        """Load the real Qwen model without touching classroom recordings."""
        try:
            settings = db.get_all_settings()
            res = whisper_worker.ensure_ready(settings.get("whisper_device") or "auto")
            return _send_json(self, {
                "ok": True,
                "device": res.get("device"),
                "used_cuda": res.get("device") == "cuda",
                "detail": res.get("detail", ""),
            })
        except Exception as e:  # noqa: BLE001
            return _send_json(self, {"ok": False, "error": str(e)})

    def _test_analysis(self):
        """真正发起一次最小推理，避免 available() 存在就误报可用。"""
        provider = None
        try:
            provider = build_provider(db.get_all_settings())
            if not provider.available():
                return _send_json(self, {
                    "ok": False,
                    "label": provider.model_label(),
                    "needs_model": isinstance(provider, llm_provider.LlamaCppSidecarLLM),
                    "error": "文字模型尚未就绪",
                })
            if isinstance(provider, llm_provider.LlamaCppSidecarLLM):
                provider.start()
            raw = provider.chat(
                "你是本地运行状态检查器，只输出JSON。",
                '请只返回 {"ok": true}，不要添加解释。',
                json_mode=True,
                temperature=0.0,
            )
            parsed = json.loads(raw)
            ok = parsed.get("ok") is True
            return _send_json(self, {"ok": ok, "label": provider.model_label(),
                                     "error": "模型返回格式不正确" if not ok else None})
        except Exception as e:  # noqa: BLE001
            return _send_json(self, {"ok": False, "error": str(e)})
        finally:
            if provider is not None:
                provider.close()

    def _whisper_status(self):
        from .ai import whisper_worker as ww
        out = {}
        for tier, info in manifest.WHISPER_TIERS.items():
            size = info["model_size"]
            v = ww.validate_model_installation(size)
            out[tier] = {
                "size": size,
                "downloaded": ww.is_model_downloaded(size),
                "valid": v.get("ok", False),
                "label": info["label"],
                "reason": v.get("reason", ""),
                "detail": v.get("detail", ""),
                "missing_files": v.get("missing_files", []),
            }
        return out

    def _validate_whisper(self):
        """Validate the one supported Qwen ASR model."""
        from .ai import whisper_worker as ww
        _read_json(self)
        results = {"Qwen3-ASR-0.6B": ww.validate_qwen_model_installation()}
        # 任意一个 broken 时给 200 但 overall 标记（前端据此显示红色提示）
        overall_ok = all(r.get("ok") for r in results.values())
        return _send_json(self, {
            "ok": overall_ok,
            "results": results,
        })

    def _cleanup_whisper(self):
        """删除指定 size 的 broken 模型缓存，让用户能重新下载。"""
        from .ai import whisper_worker as ww
        b = _read_json(self)
        size = b.get("size")
        if not size:
            return _send_json(self, {"error": "缺少 size"}, status=400)
        r = ww.cleanup_broken_model(size)
        return _send_json(self, r)

    def _download_whisper(self):
        if MODEL_PROGRESS.get("whisper", {}).get("downloading"):
            return _send_json(self, {"ok": False, "error": "正在下载中"})
        _read_json(self)
        MODEL_PROGRESS["whisper"] = {"downloading": True, "stage": "下载 Qwen3-ASR-0.6B", "done": False, "error": None}

        def _run():
            try:
                python = whisper_worker._qwen_worker_python()
                code = (
                    "from huggingface_hub import snapshot_download;import os;"
                    "snapshot_download(repo_id='Qwen/Qwen3-ASR-0.6B',"
                    "local_dir=os.environ['CLASSROOM_ASSISTANT_QWEN_MODEL_DIR'])"
                )
                env = os.environ.copy()
                env["CLASSROOM_ASSISTANT_QWEN_MODEL_DIR"] = whisper_worker.qwen_model_dir()
                result = subprocess.run(
                    [python, "-c", code], env=env, capture_output=True, text=True,
                    timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "下载失败").strip()[-800:])
                if not whisper_worker.is_model_downloaded():
                    raise RuntimeError("语音模型下载不完整，请重试")
                MODEL_PROGRESS["whisper"] = {"downloading": False, "stage": "完成", "done": True, "error": None}
            except Exception as e:  # noqa: BLE001
                MODEL_PROGRESS["whisper"] = {"downloading": False, "stage": "失败", "done": True,
                                             "error": str(e)}
        threading.Thread(target=_run, daemon=True).start()
        return _send_json(self, {"ok": True})

    def _download_llm(self):
        if MODEL_PROGRESS.get("llm", {}).get("downloading"):
            return _send_json(self, {"ok": False, "error": "正在下载中"})
        settings = db.get_all_settings()
        model_id = settings.get("llm_model_id") or manifest.default_llm_model()["id"]
        model = manifest.llm_model_by_id(model_id) or manifest.default_llm_model()
        MODEL_PROGRESS["llm"] = {"downloading": True, "stage": "准备下载", "downloaded": 0,
                                 "total": model["size_bytes"], "done": False, "error": None}

        def _run():
            try:
                sidecar = llm_provider.LlamaCppSidecarLLM(model_id)
                MODEL_PROGRESS["llm"]["stage"] = "下载 llama.cpp 服务端"
                sidecar._ensure_binary()
                MODEL_PROGRESS["llm"]["stage"] = f"下载模型 {model['display']}"

                def _cb(done, total):
                    MODEL_PROGRESS["llm"]["downloaded"] = done
                    if total:
                        MODEL_PROGRESS["llm"]["total"] = total

                from . import download as dl
                shards = model.get("shards") or [model.get("url")]
                for i, url in enumerate(shards):
                    fname = os.path.basename(url)
                    dst = os.path.join(config.get_config().dirs.llm_models, fname)
                    dl.download_file(url, dst, progress_cb=_cb if i == 0 else None,
                                     sha256=model.get("sha256") if i == 0 else None)
                    if not llm_provider._valid_gguf(dst):
                        raise RuntimeError(f"文字模型下载不完整：{fname}")
                sidecar = llm_provider.LlamaCppSidecarLLM(model_id)
                if not sidecar.is_model_downloaded():
                    raise RuntimeError("文字模型分片不完整，请重试")
                MODEL_PROGRESS["llm"] = {"downloading": False, "stage": "完成", "done": True, "error": None}
            except Exception as e:  # noqa: BLE001
                MODEL_PROGRESS["llm"] = {"downloading": False, "stage": "失败", "done": True, "error": str(e)}

        threading.Thread(target=_run, daemon=True).start()
        return _send_json(self, {"ok": True})

    def _delete_llm(self):
        settings = db.get_all_settings()
        model_id = settings.get("llm_model_id") or manifest.default_llm_model()["id"]
        model = manifest.llm_model_by_id(model_id) or manifest.default_llm_model()
        root = config.get_config().dirs.llm_models
        targets = [os.path.join(root, model["id"] + ".gguf")]
        targets.extend(os.path.join(root, os.path.basename(url)) for url in (model.get("shards") or []))
        for path in targets:
            for candidate in (path, path + ".part"):
                if os.path.isfile(candidate):
                    os.remove(candidate)
        return _send_json(self, {"ok": True})

    def _import_model(self):
        """手动导入模型（下载被网络阻断时的兜底）。

        请求：裸字节请求体；头部 X-Model-Type(llm|auto) 可选，
        X-Model-Id 指定 LLM 目标 id（默认当前设置）。
        """
        try:
            from .ai import model_import as mi
            body = _read_body(self)
            if not body:
                return _send_json(self, {"error": "空文件"}, status=400)
            mtype = (self.headers.get("X-Model-Type") or "auto").strip().lower()
            if mtype not in ("auto", "llm"):
                return _send_json(self, {"error": "X-Model-Type 非法"}, status=400)
            # 文字整理只保留当前官方 Qwen3-4B；旧版 id 不再创建新的文件名。
            model_id = manifest.default_llm_model()["id"]
            cfg = config.get_config().dirs
            llm_dir = cfg.llm_models
            res = mi.import_model_archive(
                body, model_type=mtype, model_id=model_id,
                llm_models_dir=llm_dir,
            )
            if res.get("type") == "llm":
                dest = res.get("dest", "")
                if not llm_provider._valid_gguf(dest):
                    if os.path.isfile(dest):
                        os.remove(dest)
                    raise ValueError("导入的 GGUF 文件不完整或格式不正确")
            LOG.info("手动导入模型成功: %s -> %s", mtype, res.get("dest"))
            return _send_json(self, {"ok": True, **res})
        except mi.SecurityError as e:
            LOG.warning("模型导入被安全规则拦截: %s", e)
            return _send_json(self, {"ok": False, "error": f"安全拦截: {e}"}, status=400)
        except Exception as e:  # noqa: BLE001
            LOG.exception("模型导入失败")
            return _send_json(self, {"ok": False, "error": str(e)}, status=500)

    def _restore(self):
        body = _read_body(self)
        if not body:
            return _send_json(self, {"error": "空文件"}, status=400)
        import tempfile
        tmp = os.path.join(config.get_config().dirs.exports, "restore-tmp.zip")
        with open(tmp, "wb") as f:
            f.write(body)
        try:
            res = backup.restore_backup(tmp)
            return _send_json(self, res)
        except Exception as e:  # noqa: BLE001
            return _send_json(self, {"error": str(e)}, status=500)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _delete_course(self, cid):
        # 二次确认由前端负责；这里删除课程及下属课堂（含音频/转写文件）
        for s in db.list_sessions(cid):
            self._delete_session_files(s["id"])
        db.archive_course(cid, True)  # 归档而非硬删，避免误删历史；如需硬删再扩展

    def _delete_session(self, sid):
        self._delete_session_files(sid)
        db.delete_session(sid)

    def _serve_session_audio(self, sid, index=0):
        sess = db.get_session(sid)
        files = db.audio_files_for_session(sess)
        if not files or index < 0 or index >= len(files) or not os.path.exists(files[index]["path"]):
            return _send_text(self, "无录音文件", status=404)
        self._serve_file(files[index]["path"])

    def _delete_session_audio(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "课堂记录不存在"}, status=404)
        if any(job.get("session_id") == sid for job in db.active_jobs()):
            return _send_json(self, {"error": "这节课正在处理，完成或取消后再删除录音。"}, status=409)
        segments = db.list_segments(sid)
        staged = sess.get("status") == "ready_to_process"
        if sess.get("status") != "completed" and not staged:
            return _send_json(self, {"error": "请先完成转写与总结，再删除录音。"}, status=409)
        if not segments and not staged:
            return _send_json(self, {"error": "还没有可保留的转写文字，暂不能单独删除录音。"}, status=409)

        files = db.audio_files_for_session(sess)
        paths = {f.get("path") for f in files if f.get("path")}
        if sess.get("audio_path"):
            paths.add(sess["audio_path"])
        if not paths:
            return _send_json(self, {"error": "这节课没有录音文件"}, status=404)

        # Imported recordings are copied into the app-managed audio directory.
        # Never remove a separately saved original or a path outside that tree.
        audio_root = os.path.normcase(os.path.realpath(config.get_config().dirs.audio))
        try:
            managed = all(
                os.path.commonpath([audio_root, os.path.normcase(os.path.realpath(path))]) == audio_root
                for path in paths
            )
        except ValueError:
            managed = False
        if not managed:
            return _send_json(self, {"error": "这节课的录音路径不在课堂助手内部，不能自动删除；文字结果未更改。"}, status=409)

        failures = []
        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                os.remove(path)
            except OSError as e:
                failures.append(str(e))
        if failures or any(os.path.exists(path) for path in paths):
            LOG.warning("删除课堂录音失败 %s: %s", sid, failures)
            return _send_json(self, {"error": "部分录音文件无法删除；转写与总结未更改，请关闭占用录音的程序后重试。"}, status=500)

        db.update_session(sid, audio_path=None, audio_manifest=None, audio_hash=None,
                          audio_duration_seconds=None,
                          status="completed" if segments else "waiting_upload")
        return _send_json(self, {"ok": True, "transcript_preserved": bool(segments), "audio_removed": True})

    def _delete_staged_audio_file(self, sid, index):
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "课堂记录不存在"}, status=404)
        if any(job.get("session_id") == sid for job in db.active_jobs()):
            return _send_json(self, {"error": "这节课正在处理，完成或取消后再删除录音。"}, status=409)
        if sess.get("status") != "ready_to_process":
            return _send_json(self, {
                "error": "只能逐段删除尚未处理的录音；处理完成后可删除本节全部录音并保留文字。"
            }, status=409)

        files = db.audio_files_for_session(sess)
        if index < 0 or index >= len(files):
            return _send_json(self, {"error": "录音文件不存在"}, status=404)
        target = files[index]
        audio_root = os.path.normcase(os.path.realpath(config.get_config().dirs.audio))
        try:
            managed = os.path.commonpath([
                audio_root, os.path.normcase(os.path.realpath(target["path"]))
            ]) == audio_root
        except ValueError:
            managed = False
        if not managed:
            return _send_json(self, {"error": "录音路径不在课堂助手内部，不能自动删除。"}, status=409)

        if os.path.exists(target["path"]):
            try:
                os.remove(target["path"])
            except OSError:
                return _send_json(self, {"error": "录音文件暂时无法删除，请关闭占用它的程序后重试。"}, status=500)
        if os.path.exists(target["path"]):
            return _send_json(self, {"error": "录音文件仍被占用，请关闭占用它的程序后重试。"}, status=500)

        remaining = [{key: file.get(key) for key in ("path", "name", "bytes", "duration", "hash")}
                     for file in files if file["index"] != index]
        durations = [file.get("duration") for file in remaining]
        total_duration = (sum(float(value) for value in durations)
                          if durations and all(value is not None for value in durations) else None)
        db.update_session(
            sid,
            audio_path=remaining[0]["path"] if remaining else None,
            audio_manifest=json.dumps(remaining, ensure_ascii=False) if remaining else None,
            audio_hash=_audio_collection_hash(remaining) if remaining else None,
            audio_duration_seconds=total_duration,
            status="ready_to_process" if remaining else "waiting_upload",
        )
        return _send_json(self, {
            "ok": True, "deleted_index": index, "file_count": len(remaining),
            "audio_removed": True,
        })

    def _serve_file(self, fpath):
        ctype = self._ctype(fpath)
        if fpath.lower().endswith(".webm"):
            ctype = "audio/webm"
        if ctype == "application/octet-stream" and fpath.lower().endswith((".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac")):
            ctype = "audio/" + os.path.splitext(fpath)[1].lower().lstrip(".")
        size = os.path.getsize(fpath)
        start, end, status = 0, max(0, size - 1), 200
        range_header = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip()) if range_header else None
        if match and size:
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            elif right:
                count = min(int(right), size)
                start, end = size - count, size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(fpath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _delete_session_files(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return
        self._delete_audio_files(sess)
        tdir = os.path.join(config.get_config().dirs.transcripts, sid)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir, ignore_errors=True)

    def _delete_audio_files(self, sess):
        seen = set()
        for f in db.audio_files_for_session(sess):
            path = f.get("path")
            if path and path not in seen and os.path.exists(path):
                seen.add(path)
                try:
                    os.remove(path)
                except OSError:
                    pass
        legacy = sess.get("audio_path")
        if legacy and legacy not in seen and os.path.exists(legacy):
            try:
                os.remove(legacy)
            except OSError:
                pass

    # ---------------- 静态资源 ----------------
    def _serve_static(self, p):
        if p == "/" or p == "":
            p = "/index.html"
        fpath = os.path.normpath(os.path.join(DIST_DIR, p.lstrip("/")))
        if not fpath.startswith(DIST_DIR):
            return _send_text(self, "forbidden", status=403)
        if not os.path.exists(fpath) or not os.path.isfile(fpath):
            # SPA 回退到 index.html
            fpath = os.path.join(DIST_DIR, "index.html")
            if not os.path.exists(fpath):
                return _send_text(self, "前端未构建，请先 npm run build（开发模式可单独启动 vite）", status=404)
        ctype = self._ctype(fpath)
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 前端由本地后端提供，打包后可能升级资源哈希；禁止 WebView2
        # 复用旧的 index/JS/CSS 缓存，避免升级后出现白屏。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _ctype(self, path):
        ext = os.path.splitext(path)[1].lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")


def make_server(host="127.0.0.1", port=0):
    server = ThreadingHTTPServer((host, port), ApiHandler)
    return server
