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
    handler.send_header("Access-Control-Allow-Origin", "*")
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
        m = re.match(r"/api/sessions/([\w-]+)/audio$", p)
        if m and method == "POST":
            return self._upload_audio(m.group(1))
        if m and method == "GET":
            return self._serve_session_audio(m.group(1))

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
            need_llm = ("analy" in mode) or ("full" in mode)
            if not need_whisper and not need_llm:  # 未知模式，保守要求两者
                need_whisper = need_llm = True
            settings = db.get_all_settings()
            # 缺模型时提前返回明确错误，避免创建会静默失败的幽灵任务（阶段十七）
            if need_whisper:
                size = _resolve_whisper_size(b.get("whisper_tier"), settings)
                v = whisper_worker.validate_model_installation(size)
                if not v.get("ok"):
                    return _send_json(self, {
                        "error": f"语音模型（{size}）{v.get('detail') or '缺失或不完整'}，请先到「设置 → 模型」处理。",
                        "code": "model_missing" if v.get("reason") == "no_snapshot" else "model_broken",
                        "validation": v,
                    }, status=409)
            if need_llm:
                st = detect_status(settings)
                if not provider_is_ready(settings, st):
                    return _send_json(self, {"error": "请先在「设置 → 模型」中下载文字分析模型，再开始分析。"}, status=409)
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
        from .ai import whisper_worker as ww
        s = db.get_all_settings()
        return _send_json(self, {
            "has_semester": has_sem,
            "semester": sem,
            "course_count": len(courses),
            "data_dir": config.get_config().dirs.data_dir,
            "disk": config.get_config().dirs.disk_usage(),
            "gpu": ww.gpu_info(),
            "whisper_resolved": ww.resolve_device(
                (s.get("whisper_device") or "auto")),
            "whisper_downloaded": ww.is_model_downloaded(manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"]),
            "active_jobs": db.active_jobs(),
        })

    def _session_detail(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return _send_json(self, {"error": "不存在"}, status=404)
        course = db.get_course(sess["course_id"]) or {}
        segments = db.list_segments(sid)
        topics = db.list_topics(sid)
        items = db.list_items({"course_id": sess["course_id"]})
        items = [i for i in items if i["session_id"] == sid]
        return _send_json(self, {
            "session": sess, "course": course,
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
        filename = self.headers.get("X-Filename", "audio.m4a")
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
        # 上传前先确认语音模型已就绪（严格校验 snapshot 真实可用，
        # 不只看 symlink size——避免「下载到一半断电」/「手动清理了 blobs」
        # 等假阳性导致上传后转写再次失败）。
        settings = db.get_all_settings()
        size = _resolve_whisper_size(settings.get("whisper_tier"), settings)
        v = whisper_worker.validate_model_installation(size)
        if not v.get("ok"):
            detail = v.get("detail") or "模型文件缺失或不完整"
            return _send_json(self, {
                "error": f"语音模型（{size}）{detail}。请到「设置 → 模型」点击「重新校验 / 清理后重新下载」。",
                "code": "model_missing" if v.get("reason") == "no_snapshot" else "model_broken",
                "validation": v,
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
        # 重复检测
        dup = db.read_conn().execute(
            "SELECT id, course_id, session_date FROM class_sessions WHERE audio_hash=? AND id!=?",
            (fhash, sid)).fetchone()
        db.snapshot_session(sid, "上传新录音前")
        db.update_session(sid, audio_path=dest, audio_hash=fhash,
                          audio_duration_seconds=dur, status="waiting_model",
                          source_type="audio")
        if dup:
            LOG.info("检测到相同音频已存在于其它课堂: %s", dup["id"])
        job_id, err = start_pipeline(sid, "transcribe")
        if err:
            return _send_json(self, {"error": err, "dup_with": dup["id"] if dup else None}, status=409)
        return _send_json(self, {
            "job_id": job_id,
            "duration": dur,
            "bytes": written,
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
        # 不返回明文密钥
        s.pop("openai_api_key", None)
        return _send_json(self, {
            "settings": s,
            "whisper_tiers": manifest.WHISPER_TIERS,
            "llm_models": manifest.LLM_MODELS,
            "provider_status": detect_status(s),
            "whisper_device_options": [
                {"value": "auto", "label": "自动（GPU 可用时优先，否则 CPU）"},
                {"value": "cuda", "label": "优先 GPU（faster-whisper CUDA）"},
                {"value": "cpu", "label": "仅 CPU（最稳）"},
            ],
            "whisper_gpu": whisper_worker.gpu_info(),
            "whisper_resolved": whisper_worker.resolve_device(
                (s.get("whisper_device") or "auto")),
        })

    def _settings_put(self):
        b = _read_json(self)
        for k, v in b.items():
            if k == "openai_api_key":
                if v:
                    from .credential import set_credential
                    set_credential("openai_api_key", v)
                else:
                    from .credential import delete_credential
                    delete_credential("openai_api_key")
                db.set_setting("openai_api_key_set", "1" if v else "0")
                continue
            if isinstance(v, bool):
                db.set_setting(k, "1" if v else "0")
            elif isinstance(v, (int, float)):
                db.set_setting(k, str(v))
            else:
                db.set_setting(k, v)
        return _send_json(self, {"ok": True})

    def _test_whisper(self):
        """真实加载模型并转写短音频，而不是只检查目录是否存在。"""
        try:
            import tempfile
            from . import selftest

            # 系统 %TEMP% 可能被安全软件或受限账户禁止写入；应用的数据目录
            # 已在启动时验证可写，用它承载一次性测试文件更可靠。
            with tempfile.TemporaryDirectory(
                prefix="ca_whisper_check_", dir=config.get_config().dirs.data_dir
            ) as tmp:
                settings = db.get_all_settings()
                size = _resolve_whisper_size(settings.get("whisper_tier"), settings)
                res = selftest._check_whisper_ready(tmp, size=size)
            return _send_json(self, {
                "ok": bool(res.get("ready")),
                "gpu": whisper_worker.gpu_info(),
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
        """对所有已列出的 whisper tier 做严格校验，返回每档的 ok/reason/detail。"""
        from .ai import whisper_worker as ww
        b = _read_json(self)
        sizes = b.get("sizes") if isinstance(b, dict) and b.get("sizes") else None
        if not sizes:
            sizes = [info["model_size"] for info in manifest.WHISPER_TIERS.values()]
        results = {s: ww.validate_model_installation(s) for s in sizes}
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
        b = _read_json(self)
        size = b.get("size") or manifest.WHISPER_TIERS[manifest.DEFAULT_WHISPER_TIER]["model_size"]
        MODEL_PROGRESS["whisper"] = {"downloading": True, "stage": "下载语音模型中", "done": False, "error": None}
        # 与 whisper_worker.models_root() 保持同源，否则会出现「下载完成了却仍提示未下载」
        cache = whisper_worker.models_root()

        def _run():
            try:
                from faster_whisper import WhisperModel
                WhisperModel(size, device="cpu", compute_type="int8",
                             download_root=cache)
                if not whisper_worker.is_model_downloaded(size):
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

        请求：裸字节请求体；头部 X-Model-Type(llm|whisper|auto) 可选，
        X-Model-Id 指定 LLM 目标 id（默认当前设置）。
        """
        try:
            from .ai import model_import as mi
            body = _read_body(self)
            if not body:
                return _send_json(self, {"error": "空文件"}, status=400)
            mtype = (self.headers.get("X-Model-Type") or "auto").strip().lower()
            if mtype not in ("auto", "llm", "whisper"):
                return _send_json(self, {"error": "X-Model-Type 非法"}, status=400)
            model_id = (self.headers.get("X-Model-Id") or "").strip()
            if not model_id:
                model_id = (db.get_all_settings().get("llm_model_id")
                            or manifest.default_llm_model()["id"])
            cfg = config.get_config().dirs
            llm_dir = cfg.llm_models
            # 与 whisper_worker.models_root() 同源（手动导入后必须能被 is_model_downloaded 识别）
            whisper_dir = whisper_worker.models_root()
            res = mi.import_model_archive(
                body, model_type=mtype, model_id=model_id,
                llm_models_dir=llm_dir, whisper_models_dir=whisper_dir,
            )
            if res.get("type") == "llm":
                dest = res.get("dest", "")
                if not llm_provider._valid_gguf(dest):
                    if os.path.isfile(dest):
                        os.remove(dest)
                    raise ValueError("导入的 GGUF 文件不完整或格式不正确")
            elif res.get("type") == "whisper":
                sizes = [x["model_size"] for x in manifest.WHISPER_TIERS.values()]
                sizes.extend(manifest.WHISPER_EXTRA_SIZES)
                if not any(whisper_worker.is_model_downloaded(size) for size in sizes):
                    raise ValueError("导入包中没有找到完整的 faster-whisper 模型")
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

    def _serve_session_audio(self, sid):
        sess = db.get_session(sid)
        if not sess or not sess.get("audio_path") or not os.path.exists(sess["audio_path"]):
            return _send_text(self, "无录音文件", status=404)
        self._serve_file(sess["audio_path"])

    def _serve_file(self, fpath):
        ctype = self._ctype(fpath)
        if ctype == "application/octet-stream" and fpath.lower().endswith((".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac")):
            ctype = "audio/" + os.path.splitext(fpath)[1].lower().lstrip(".")
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(data)

    def _delete_session_files(self, sid):
        sess = db.get_session(sid)
        if not sess:
            return
        if sess.get("audio_path") and os.path.exists(sess["audio_path"]):
            try:
                os.remove(sess["audio_path"])
            except OSError:
                pass
        tdir = os.path.join(config.get_config().dirs.transcripts, sid)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir, ignore_errors=True)

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
