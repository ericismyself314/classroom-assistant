"""单实例保护（需求文档阶段十五）。

- Windows：全局命名互斥体（Global\\ClassroomAssistant-SingleInstance），进程退出自动释放。
- 其它平台：PID 锁文件（含失效检测）。
- 第二份实例启动时会通过本机控制端口向第一份发送 focus，由第一份聚焦已有窗口。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading

_APP_ID = "ClassroomAssistant-SingleInstance"
_control: dict = {"mutex": None, "lock": None, "server": None, "stop": False}


def _data_dir() -> str:
    from . import config

    d = config.get_config().dirs.data_dir
    os.makedirs(d, exist_ok=True)
    return d


def acquire() -> bool:
    """返回 True 表示这是第一个实例；False 表示已有实例在运行。"""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, 0, f"Global\\{_APP_ID}")
            if not mutex:
                return True  # 创建失败则退化为允许多开，避免误杀
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(mutex)
                return False
            _control["mutex"] = mutex
            return True
        except Exception:  # noqa: BLE001
            return True
    # 非 Windows：PID 锁文件（失效检测）
    d = _data_dir()
    lock = os.path.join(d, ".single_instance.lock")
    if os.path.exists(lock):
        try:
            with open(lock) as f:
                pid = int(f.read().strip() or 0)
            if pid and _pid_alive(pid):
                return False
        except Exception:  # noqa: BLE001
            pass
        try:
            os.remove(lock)
        except Exception:  # noqa: BLE001
            pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        _control["lock"] = lock
        return True
    except FileExistsError:
        return False


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED
            if handle:
                kernel32.CloseHandle(handle)
                return True
        except Exception:  # noqa: BLE001
            return False
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def release() -> None:
    if sys.platform == "win32" and _control.get("mutex"):
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(_control["mutex"])
        except Exception:  # noqa: BLE001
            pass
    elif _control.get("lock") and os.path.exists(_control["lock"]):
        try:
            os.remove(_control["lock"])
        except Exception:  # noqa: BLE001
            pass
    _control["stop"] = True


def start_control_server(focus_fn) -> None:
    """第一份实例启动本机控制端口，接收 focus 信号后聚焦窗口。"""
    d = _data_dir()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", 0))
    except Exception:  # noqa: BLE001
        return
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        with open(os.path.join(d, "control_port.json"), "w", encoding="utf-8") as f:
            json.dump({"port": port}, f)
    except Exception:  # noqa: BLE001
        pass
    _control["server"] = srv

    def loop() -> None:
        while not _control["stop"]:
            try:
                srv.settimeout(1.0)
                conn, _ = srv.accept()
            except Exception:  # noqa: BLE001
                continue
            try:
                data = conn.recv(32)
                if data.strip().lower().startswith(b"focus") and focus_fn:
                    try:
                        focus_fn()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=loop, daemon=True).start()


def signal_focus() -> None:
    """第二份实例：向第一份发送 focus 信号。"""
    p = os.path.join(_data_dir(), "control_port.json")
    if not os.path.exists(p):
        return
    try:
        with open(p) as f:
            port = json.load(f).get("port")
        if not port:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        s.sendall(b"focus")
        s.close()
    except Exception:  # noqa: BLE001
        pass
