"""课堂助手 启动入口。

- 初始化配置、数据库，标记重启中断的任务；
- 在 127.0.0.1 随机端口启动本地后端（同源提供前端与 API）；
- 打开原生 WebView2 窗口；关闭窗口时清理 sidecar 进程并关闭数据库；
- 支持 --no-gui 仅启动后端（用于测试 / 命令行）。
"""
from __future__ import annotations

import sys
import threading
import time
import os

from backend import config, db
from backend.ai import llm_provider
from backend.api import make_server
from backend.logutil import get_logger

LOG = get_logger("main")


def main() -> None:
    # Windows 控制台默认 GBK，报告含中文/emoji 会抛 UnicodeEncodeError；
    # 统一用 UTF-8 输出（失败则替换），避免打包后 --self-test / 日志打印崩溃。
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    if "--self-test" in sys.argv:
        _self_test()
        return

    if "--readiness-check" in sys.argv:
        _readiness_check()
        return

    cfg = config.init_config()

    # 单实例保护（阶段十五）：第二份实例请求聚焦已有窗口后退出。
    from backend.single_instance import (
        acquire,
        release,
        signal_focus,
        start_control_server,
    )

    # 打包冒烟测试可能与用户正在使用的安装版同时运行；只在显式测试环境
    # 中跳过全局互斥体，正常启动仍保持单实例保护。
    if os.environ.get("CLASSROOM_ASSISTANT_TEST_ALLOW_MULTI_INSTANCE") != "1":
        if not acquire():
            signal_focus()
            sys.exit(0)

    db.init_db()
    db.mark_interrupted_jobs()

    try:
        server, port, url = _start_backend()
    except Exception as e:  # noqa: BLE001
        LOG.exception("后端启动失败")
        _show_backend_error(str(e))
        release()
        sys.exit(1)

    if "--no-gui" in sys.argv:
        start_control_server(lambda: None)
        # windowed PyInstaller 程序没有可读取的 stdout。打包冒烟测试通过
        # 独立临时文件取得随机端口，避免猜端口或接触用户数据。
        _write_test_url_file(url)
        print(url, flush=True)
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            pass
        _shutdown(server)
        release()
        return

    try:
        import webview

        def _focus_window() -> None:
            # pywebview 的 Window.focus 是布尔属性，不是可调用方法；旧代码
            # 调用它会静默失败，导致第二次点桌面图标时既不开新窗口，也无法
            # 唤回已有窗口。show + restore 由 WinForms 端安全地切回前台。
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    if webview.windows:
                        window = webview.windows[0]
                        try:
                            window.restore()
                        except Exception:  # noqa: BLE001
                            pass
                        window.show()
                        LOG.info("已唤回桌面窗口")
                        return
                except Exception:  # noqa: BLE001
                    LOG.exception("唤回桌面窗口失败")
                    return
                time.sleep(0.1)
            LOG.warning("收到聚焦请求，但桌面窗口尚未就绪")

        start_control_server(_focus_window)
        # pywebview 默认启用 private_mode，每次双击都会新建临时 WebView2
        # 用户数据目录。Windows 上这会反复触发 WebView2 冷启动，且临时目录
        # 创建失败时还会出现“无法创建数据目录”。使用应用自己的可写目录，
        # 让 WebView2 复用缓存和配置，后续启动明显更快。
        # v2 使用全新的用户数据目录，避开旧版可能留下的损坏/陈旧缓存；
        # 后续静态资源同时通过 no-store 响应头避免版本升级后白屏。
        webview_profile = os.path.join(cfg.dirs.data_dir, "webview-v2")
        try:
            os.makedirs(webview_profile, exist_ok=True)
        except OSError as e:
            # 配置目录已在 init_config 中创建；若极端情况下不可写，仍允许
            # pywebview 回退到系统默认目录，不能因此阻止应用打开。
            LOG.warning("WebView2 数据目录创建失败，将回退默认目录: %s", e)
            webview_profile = None

        ui_started_at = time.perf_counter()
        window = webview.create_window(
            "课堂助手", url, width=1280, height=820, min_size=(1024, 680)
        )

        def _log_window_shown(*_args) -> None:
            LOG.info("桌面窗口已显示（耗时 %.2fs）", time.perf_counter() - ui_started_at)

        def _log_page_loaded(*_args) -> None:
            LOG.info("前端页面已加载（耗时 %.2fs）", time.perf_counter() - ui_started_at)

        window.events.shown += _log_window_shown
        window.events.loaded += _log_page_loaded
        LOG.info("开始初始化桌面窗口（WebView2 数据目录: %s）", webview_profile or "系统默认")
        start_kwargs = {"private_mode": False}
        if webview_profile:
            start_kwargs["storage_path"] = webview_profile
        webview.start(**start_kwargs)
    except Exception as e:  # noqa: BLE001
        LOG.exception("GUI 启动失败")
        print("GUI 启动失败，可改用 --no-gui 模式：", e)
    _shutdown(server)
    release()


def _write_test_url_file(url: str) -> None:
    """仅在显式设置测试环境变量时，把后端 URL 写入指定文件。"""
    import os

    path = os.environ.get("CLASSROOM_ASSISTANT_TEST_URL_FILE", "").strip()
    if not path:
        return
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(url)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        LOG.exception("写入测试端口文件失败")


def _shutdown(server=None) -> None:
    LOG.info("正在关闭应用...")
    llm_provider.shutdown_all()
    if server is not None:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass
    db.close_all_connections()


def _start_backend():
    """启动本地后端并等待健康检查通过（阶段十四：启动/关闭逻辑）。

    返回 (server, port, url)；若多次重试仍无响应则抛出 RuntimeError。
    """
    import time
    import urllib.request

    server = make_server("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    LOG.info("后端已启动: %s", url)

    last_err = ""
    for _ in range(50):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return server, port, url
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
        time.sleep(0.2)
    raise RuntimeError(f"后端健康检查失败（/api/health 无响应）：{last_err}")


def _show_backend_error(detail: str) -> None:
    """后端启动失败时展示中文错误页（含日志路径与重试），阶段十四要求。"""
    import html as _html
    import os

    try:
        log_dir = config.get_config().dirs.logs
    except Exception:  # noqa: BLE001
        log_dir = ""

    safe_detail = _html.escape(detail)
    safe_log = _html.escape(log_dir)
    err_html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,'Microsoft YaHei',sans-serif;margin:0;background:#1e1f22;color:#e8eaed;padding:32px}}
 h2{{margin-top:0;color:#ff6b5e}}
 code{{background:#2c2e33;padding:2px 6px;border-radius:4px;color:#9fd0ff}}
 ol{{line-height:1.9;color:#c8ccd2}}
 button{{margin-top:18px;background:#2f6bff;color:#fff;border:none;padding:10px 18px;border-radius:8px;font-size:14px;cursor:pointer}}
 button.ghost{{background:transparent;border:1px solid #4a4d54;color:#c8ccd2}}
</style></head><body>
<h2>课堂助手启动失败</h2>
<p>应用未能正常启动本地服务，原因：<br><code>{safe_detail}</code></p>
<p>排查与重试步骤：</p>
<ol>
  <li>确认已安装 <b>Microsoft Edge WebView2 运行时</b>（系统自带的 Edge 一般已包含）。</li>
  <li>打开终端，进入安装目录后运行 <code>ClassroomAssistant.exe --self-test</code> 查看自检报告。</li>
  <li>日志目录：<code>{safe_log}</code>（可打包后反馈）。</li>
  <li>点击下方「重试 / 重新启动」即可再次尝试；或关闭窗口后手动重启应用。</li>
</ol>
<button onclick="pywebview.api.restart()">重试 / 重新启动</button>
<button class="ghost" style="margin-left:10px" onclick="pywebview.api.close()">关闭</button>
</body></html>"""

    class _ErrorApi:
        def restart(self):  # 重新启动：用同一进程镜像原地替换，等价于重试
            import os as _os
            import sys as _sys

            _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

        def close(self):
            pass  # 由 JS window 关闭触发 webview.start() 返回

    try:
        import webview

        webview.create_window("课堂助手 - 启动失败", html=err_html, width=760, height=540, js_api=_ErrorApi())
        webview.start()
    except Exception:  # noqa: BLE001
        print("后端启动失败：", detail)


def _self_test() -> None:
    """--self-test：环境基础自检，输出报告并以退出码反映致命项（0=通过，2=失败）。"""
    from backend.selftest import format_report, run_self_test

    frozen = getattr(sys, "frozen", False)
    report = run_self_test(fatal_on_missing_static=frozen)
    print(format_report(report))
    sys.exit(0 if report["ok"] else 2)


def _readiness_check() -> None:
    """--readiness-check：真实模型就绪检查，按退出码区分结果。

    0=就绪(GPU) / 10=基础通过但模型缺失 / 11=语音不可用 / 12=文字不可用 /
    13=就绪(CPU) / 20=数据库或核心错误 / 21=前端缺失 / 22=后端启动失败。
    """
    from backend.selftest import format_readiness_report, run_readiness_check

    frozen = getattr(sys, "frozen", False)
    report = run_readiness_check(fatal_on_missing_static=frozen)
    print(format_readiness_report(report))
    sys.exit(report.get("exit_code", 20))


if __name__ == "__main__":
    main()
