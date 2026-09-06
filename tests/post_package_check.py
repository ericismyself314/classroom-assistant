#!/usr/bin/env python
"""打包后真实测试（阶段十七）。

对「已构建的 ClassroomAssistant.exe」做冒烟级端到端验证：
  1. --self-test 必须 EXIT=0
  2. --readiness-check 必须 EXIT=10（基础通过、模型缺失）
  3. 启动 --no-gui，轮询 /api/health 直到 200
  4. /api/readiness、/api/state 返回结构正确
  5. 创建 学期→课程→课堂，GET 课堂详情正常
  6. 在未下载模型时调用 /process，应返回清晰错误（而非崩溃/500 未知错误）
  7. 清理上述临时数据
  8. 前端 /index.html 与其中引用的 /assets/*.js、*.css 全部 200 + 正确 Content-Type
     （这是「双击 exe 白屏」最直接的回归点）

真实「转写 + 分析」需要本机模型（首次使用在设置里下载），沙箱中无法下载，
故第 6 步只验证「缺模型时的明确报错路径」，完整 AI 流程需在真实 Windows 复核。

用法：
  python tests/post_package_check.py [exe_path]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXE = os.path.normpath(os.path.join(HERE, "..", "dist", "ClassroomAssistant", "ClassroomAssistant.exe"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str) -> None:
    print(msg, flush=True)


def run_cli(exe: str, arg: str, env: dict[str, str]) -> int:
    p = subprocess.run([exe, arg], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode


def wait_health(base: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def get_json(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(base: str, path: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", errors="replace"))


def delete(base: str, path: str):
    req = urllib.request.Request(base + path, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


_ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""")
_EXPECTED_CT = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
}


def check_frontend_served(base: str) -> tuple[bool, str]:
    """GET / 拿 index.html，再取出其中所有 script/link 资源 GET 一遍，全部 200 才行。

    返回 (ok, detail)；detail 既给运维看也给日志看。
    """
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            if r.status != 200:
                return False, f"GET / 返回 {r.status}"
            ctype = r.headers.get("Content-Type", "")
            if "text/html" not in ctype:
                return False, f"GET / Content-Type 非 HTML：{ctype}"
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"GET / 异常：{e}"

    # 抓出所有本地相对资源（忽略 http(s)/data/锚点），并按浏览器规则解析为绝对路径
    # （浏览器拿到 "./assets/foo.js" 会自动解析为 "/assets/foo.js"；urlopen 不会，
    #  直接 GET 会变成 /./assets/... 而服务端返回 502——这不是真实的白屏场景，
    #  但错误信号需要正确处理）
    assets: list[str] = []
    base_url = base.rstrip("/") + "/"
    for m in _ASSET_RE.finditer(html):
        u = m.group(1)
        if u.startswith(("http://", "https://", "data:", "#", "//")):
            continue
        # urljoin 还会吸收掉锚点 / 查询串
        resolved = urllib.parse.urljoin(base_url, u)
        path = urllib.parse.urlparse(resolved).path
        if path and path not in assets:
            assets.append(path)

    if not assets:
        return False, "index.html 中未找到任何本地资源（Vite 应注入 /assets/*.js）"

    bad: list[str] = []
    for u in assets:
        try:
            with urllib.request.urlopen(base + u, timeout=5) as r:
                if r.status != 200:
                    bad.append(f"{u} -> {r.status}")
                    continue
                ctype = r.headers.get("Content-Type", "")
                ext = os.path.splitext(u)[1].lower()
                want = _EXPECTED_CT.get(ext)
                if want and want not in ctype:
                    bad.append(f"{u} Content-Type={ctype}（期望含 {want}）")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{u} 异常：{e}")

    if bad:
        return False, f"{len(bad)}/{len(assets)} 资源异常：" + "; ".join(bad)
    return True, f"✅ / + {len(assets)} 资源全部 200（{', '.join(assets[:3])}...）"


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE
    if not os.path.isfile(exe):
        log(f"❌ 找不到可执行文件：{exe}")
        return 2

    fails = []
    # 无条件使用独立数据根，不能把打包测试的学期、课程、日志或模型占位写进
    # 用户真实的 %LOCALAPPDATA%\ClassroomAssistant。
    isolated_root = tempfile.mkdtemp(prefix="ca_pkg_check_")
    import atexit

    atexit.register(shutil.rmtree, isolated_root, True)
    child_env = os.environ.copy()
    child_env["LOCALAPPDATA"] = os.path.join(isolated_root, "Local")
    child_env["APPDATA"] = os.path.join(isolated_root, "Roaming")
    child_env["PYTHONUTF8"] = "1"
    # 本机可能已有正式安装版运行；仅本次隔离测试跳过全局单实例锁。
    child_env["CLASSROOM_ASSISTANT_TEST_ALLOW_MULTI_INSTANCE"] = "1"
    url_file = os.path.join(isolated_root, "backend-url.txt")
    child_env["CLASSROOM_ASSISTANT_TEST_URL_FILE"] = url_file

    # 1) self-test
    rc = run_cli(exe, "--self-test", child_env)
    log(f"[1] --self-test EXIT={rc} (期望 0)")
    if rc != 0:
        fails.append("self-test 未返回 0")

    # 2) readiness-check
    rc = run_cli(exe, "--readiness-check", child_env)
    log(f"[2] --readiness-check EXIT={rc} (期望 10)")
    if rc != 10:
        fails.append("readiness-check 未返回 10")

    # 3-6) 启动后端并冒烟测试
    proc = subprocess.Popen([exe, "--no-gui"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=child_env)
    try:
        # windowed PyInstaller 程序没有可读取的 stdout；由 --no-gui 在
        # 显式测试环境变量指定的临时文件中写出随机端口。
        url = None
        deadline = time.time() + 20
        while time.time() < deadline and proc.poll() is None:
            try:
                if os.path.isfile(url_file):
                    with open(url_file, encoding="utf-8") as f:
                        candidate = f.read().strip()
                    if candidate.startswith("http://127.0.0.1:"):
                        url = candidate
                        break
            except OSError:
                pass
            time.sleep(0.1)
        if not url:
            stderr = proc.stderr.read() if proc.poll() is not None and proc.stderr else ""
            log(f"❌ 未能取得打包后端地址；进程退出码={proc.poll()} stderr={stderr[:300]!r}")
            fails.append("未能取得打包后端地址")
            return 1
        if not wait_health(url, timeout=15):
            log("❌ [3] 后端健康检查失败，无法继续")
            fails.append("后端健康检查失败")
            return 1

        base = url
        # 重新确认能拿到 health（readline 可能没抓到）
        if not wait_health(base, timeout=5):
            log("❌ [3] 后端健康检查失败")
            fails.append("后端健康检查失败")
        else:
            log("[3] ✅ /api/health = 200")

        rd = get_json(base, "/api/readiness")
        ok = all(k in rd for k in ("whisper_downloaded", "llm_downloaded", "webview2", "gpu", "frontend_present"))
        log(f"[4] /api/readiness 结构{'完整' if ok else '缺失字段'}: {list(rd.keys())}")
        if not ok:
            fails.append("/api/readiness 结构不完整")

        st = get_json(base, "/api/state")
        log(f"[4b] /api/state.has_semester={st.get('has_semester')}")

        # 5) 创建 学期→课程→课堂
        sem_name = "PKG_TEST_" + str(int(time.time()))
        sc, sem = post_json(base, "/api/semesters", {
            "name": sem_name, "start_date": "2026-09-01", "end_date": "2027-01-31",
            "timezone": "Asia/Hong_Kong", "activate": True,
        })
        if sc != 200 or "id" not in sem:
            log(f"❌ [5] 创建学期失败：{sc} {sem}")
            fails.append("创建学期失败")
        else:
            sem_id = sem["id"]
            cc, course = post_json(base, "/api/courses", {
                "semester_id": sem_id, "name": "测试课", "language_mode": "auto",
                "color": "#4CAF50", "slots": [],
            })
            if cc != 200 or "id" not in course:
                log(f"❌ [5] 创建课程失败：{cc} {course}")
                fails.append("创建课程失败")
            else:
                course_id = course["id"]
                pc, sess = post_json(base, "/api/sessions", {
                    "course_id": course_id, "title": "测试课堂", "session_date": "2026-09-10",
                })
                if pc != 200 or "id" not in sess:
                    log(f"❌ [5] 创建课堂失败：{pc} {sess}")
                    fails.append("创建课堂失败")
                else:
                    sess_id = sess["id"]
                    det = get_json(base, f"/api/sessions/{sess_id}")
                    # 详情返回结构是 {session, course, segments, topics, items}
                    det_title = (det.get("session") or {}).get("title")
                    log(f"[5] ✅ 学期/课程/课堂创建成功，课堂详情标题={det_title!r}")
                    if det_title != "测试课堂":
                        log(f"❌ [5] 课堂详情标题不符（期望 '测试课堂'）")
                        fails.append("课堂详情标题不符")
                    # 6) 未下载模型时 /process 必须明确拒绝（409），不得创建幽灵任务
                    pc2, resp2 = post_json(base, f"/api/sessions/{sess_id}/process",
                                           {"mode": "transcribe", "whisper_tier": "balanced"})
                    err_text = json.dumps(resp2, ensure_ascii=False)
                    clear = (pc2 == 409) and ("模型" in err_text)
                    log(f"[6] /process 缺模型时返回 {pc2} -> {err_text[:120]}")
                    if not clear:
                        fails.append(f"/process 缺模型未明确拒绝（{pc2}）")
                    delete(base, f"/api/sessions/{sess_id}")
                delete(base, f"/api/courses/{course_id}")
        log("[7] 使用隔离数据目录；进程退出后整体清理，不接触用户真实数据库")

        # 8) 前端静态资源必须能被打包后的 exe 正确托管（白屏的直接回归点）
        ok, detail = check_frontend_served(base)
        log(f"[8] 前端静态资源：{detail}")
        if not ok:
            fails.append(f"前端静态资源未被正确托管：{detail}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    if fails:
        log(f"\n❌ 失败项（{len(fails)}）：{fails}")
        return 1
    log("\n✅ 打包后冒烟测试全部通过（注：真实 AI 转写+分析需本机模型，沙箱未验证）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
