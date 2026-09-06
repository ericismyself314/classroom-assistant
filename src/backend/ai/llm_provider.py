"""本地文字模型 Provider。

三种实现（见需求文档第九章）：
1. LlamaCppSidecarLLM：应用自管的 llama.cpp 服务端 sidecar（下载 exe + GGUF，仅 127.0.0.1，
   关闭即结束进程，不向局域网暴露）。默认优先。
2. OllamaLLM：本机已有的 Ollama（自动检测，中文安装引导在文档中给出）。兼容方式。
3. OpenAICompatibleLLM：可选高级设置，默认关闭；API 密钥存于 DPAPI 凭据库，不落库/日志。

统一接口：chat(system, user, json_mode, temperature) -> str（模型原始输出）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from collections import deque

from ..config import get_config
from ..credential import get_credential
from ..logutil import get_logger
from .. import download as dl
from . import manifest

LOG = get_logger("llm")

_MIN_GGUF_BYTES = 10 * 1024 * 1024
# 新版 llama.cpp 的 exe 是很小的启动壳，主体位于 *-impl.dll；不能再用
# 1 MB 阈值判断完整性，需与核心 DLL 和完成标记联合验证。
_MIN_SIDECAR_BYTES = 4 * 1024


def _valid_gguf(path: str) -> bool:
    try:
        if os.path.getsize(path) < _MIN_GGUF_BYTES:
            return False
        with open(path, "rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def _valid_windows_exe(path: str) -> bool:
    try:
        if os.path.getsize(path) < _MIN_SIDECAR_BYTES:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


_SPAWWN_LOCK = threading.Lock()


def _spawn_local_server(args):
    """External llama.cpp must not inherit the frozen app's DLL search path."""
    env = os.environ.copy()
    bundle = getattr(sys, "_MEIPASS", None)
    frozen_windows = sys.platform == "win32" and getattr(sys, "frozen", False) and bundle
    if frozen_windows:
        bundle_norm = os.path.normcase(os.path.abspath(bundle))
        env["PATH"] = os.pathsep.join(
            part for part in env.get("PATH", "").split(os.pathsep)
            if part and not (os.path.normcase(os.path.abspath(part)) == bundle_norm
                or os.path.normcase(os.path.abspath(part)).startswith(bundle_norm + os.sep))
        )
    with _SPAWWN_LOCK:
        if frozen_windows:
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(
                args, cwd=os.path.dirname(args[0]), env=env,
                # A windowed EXE has no stdin/console. Give the child real handles
                # and keep the helper invisible to the user.
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        finally:
            if frozen_windows:
                ctypes.windll.kernel32.SetDllDirectoryW(bundle)


class BaseLLM:
    name = "base"

    def chat(self, system, user, json_mode=True, temperature=0.2) -> str:
        raise NotImplementedError

    def available(self) -> bool:
        return False

    def model_label(self) -> str:
        return self.name

    def close(self) -> None:
        pass


class OllamaLLM(BaseLLM):
    def __init__(self, model: str):
        # 直接使用 IPv4，避免 Windows 对 localhost 先尝试 IPv6、再尝试
        # IPv4，导致 Ollama 未启动时每次状态检查额外等待约 0.5 秒。
        self.base = "http://127.0.0.1:11434"
        self.model = model or "qwen2.5:latest"
        self.name = "ollama"

    def available(self, timeout: float = 3) -> bool:
        try:
            r = urllib.request.urlopen(self.base + "/api/tags", timeout=timeout)
            return r.status == 200
        except Exception:
            return False

    def list_models(self, timeout: float = 5) -> list[str]:
        try:
            d = json.load(urllib.request.urlopen(self.base + "/api/tags", timeout=timeout))
            return [m["name"] for m in d.get("models", [])]
        except Exception:
            return []

    def chat(self, system, user, json_mode=True, temperature=0.2) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        req = urllib.request.Request(
            self.base + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            return json.loads(resp.read().decode("utf-8"))["message"]["content"]

    def model_label(self) -> str:
        return f"Ollama: {self.model}"


class OpenAICompatibleLLM(BaseLLM):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"
        self.name = "openai"

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system, user, json_mode=True, temperature=0.2) -> str:
        import httpx

        body = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=900,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def model_label(self) -> str:
        return f"API: {self.model}"


class LlamaCppSidecarLLM(BaseLLM):
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model = manifest.llm_model_by_id(model_id) or manifest.default_llm_model()
        self.name = "sidecar"
        self.proc = None
        self.port = None
        self.base = None
        self.gguf = None

    def _bin_path(self) -> str:
        return os.path.join(get_config().dirs.llm_bin, manifest.SIDECAR["exe_name"])

    def is_binary_downloaded(self) -> bool:
        root = get_config().dirs.llm_bin
        return (
            _valid_windows_exe(self._bin_path())
            and os.path.isfile(os.path.join(root, "llama.dll"))
            and os.path.isfile(os.path.join(root, "ggml.dll"))
            and os.path.isfile(os.path.join(root, "llama-server-impl.dll"))
            and os.path.isfile(os.path.join(root, ".complete"))
        )

    def _model_files_complete(self, paths: list[str]) -> bool:
        """Check GGUF headers and the manifest size before advertising readiness.

        A previous download could leave a large, header-valid prefix behind.  The
        old 10 MiB check treated that prefix as a usable model, so re-analysis
        reached llama.cpp and failed with a vague model-loading error.  Manifest
        sizes are approximate, therefore allow a small tolerance while still
        rejecting obviously truncated files.
        """
        if not paths or not all(_valid_gguf(p) for p in paths):
            return False
        expected = int(self.model.get("size_bytes") or 0)
        if expected <= 0:
            return True
        try:
            actual = sum(os.path.getsize(p) for p in paths)
        except OSError:
            return False
        return actual >= int(expected * 0.95)

    def is_model_downloaded(self) -> bool:
        root = get_config().dirs.llm_models
        imported = os.path.join(root, self.model["id"] + ".gguf")
        if self._model_files_complete([imported]):
            return True
        shards = self.model.get("shards") or []
        original_paths = [os.path.join(root, os.path.basename(url)) for url in shards]
        return bool(shards) and self._model_files_complete(original_paths)

    def _ensure_binary(self) -> str:
        exe = self._bin_path()
        if self.is_binary_downloaded():
            return exe
        if os.path.exists(exe):
            os.remove(exe)
        marker = os.path.join(get_config().dirs.llm_bin, ".complete")
        if os.path.exists(marker):
            os.remove(marker)
        urls = self._latest_sidecar_asset_urls()
        for index, url in enumerate(urls):
            tmp = exe + f".download.{index}.zip"
            dl.download_file(url, tmp)
            self._extract_windows_binaries(tmp)
            os.remove(tmp)
        with open(marker, "w", encoding="ascii") as f:
            f.write("ok\n")
        if not self.is_binary_downloaded():
            raise RuntimeError("llama.cpp 服务端下载不完整，请重试")
        return exe

    def _extract_windows_binaries(self, archive: str) -> None:
        """从官方 zip 提取 exe/dll 到同一目录，包含主程序依赖与 CUDA runtime。"""
        root = get_config().dirs.llm_bin
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                basename = os.path.basename(info.filename.replace("\\", "/"))
                if not basename or not basename.lower().endswith((".exe", ".dll")):
                    continue
                dest = os.path.join(root, basename)
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

    def _latest_sidecar_asset_url(self) -> str:
        return self._latest_sidecar_asset_urls()[0]

    def _latest_sidecar_asset_urls(self) -> list[str]:
        api = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5"
        with urllib.request.urlopen(api, timeout=30) as resp:
            rels = json.load(resp)
        # 资产命名形如 llama-bXXXX-bin-win-cuda-12.4-x64.zip，
        # 主包含 llama-server.exe；cudart-* 只是运行库，不能误当成主包。
        candidates: list[tuple[str, str, list[dict]]] = []
        for rel in rels:
            for a in rel.get("assets", []):
                name = a["name"].lower()
                if (
                    name.endswith(".zip")
                    and "bin-win-cuda" in name
                    and "x64" in name
                    and not name.startswith("cudart-")
                ):
                    candidates.append((name, a["browser_download_url"], rel.get("assets", [])))
        if not candidates:
            for rel in rels:
                for a in rel.get("assets", []):
                    name = a["name"].lower()
                    if (
                        name.endswith(".zip")
                        and "bin-win" in name
                        and "x64" in name
                        and not name.startswith("cudart-")
                    ):
                        candidates.append((name, a["browser_download_url"], rel.get("assets", [])))
        if not candidates:
            raise RuntimeError("未找到 llama.cpp 服务端二进制（Windows CUDA x64）")
        # CUDA 12.4 兼容当前支持的 NVIDIA 驱动范围，优先于 CUDA 13。
        candidates.sort(key=lambda item: ("cuda-12.4" not in item[0], item[0]))
        main_name, main_url, assets = candidates[0]
        urls = [main_url]
        if "-bin-win-cuda-" in main_name:
            flavor = main_name.split("-bin-win-", 1)[1]
            runtime_name = "cudart-llama-bin-win-" + flavor
            runtime = next(
                (a["browser_download_url"] for a in assets if a["name"].lower() == runtime_name),
                None,
            )
            if runtime:
                urls.append(runtime)
        return urls

    def _ensure_model(self) -> str:
        m = self.model
        shards = m.get("shards") or [m.get("url")]
        # 分片模型：下载所有分片；llama.cpp 会自动加载同名兄弟分片。
        # gguf 指针指向第一个分片。
        root = get_config().dirs.llm_models
        imported = os.path.join(root, m["id"] + ".gguf")
        if self._model_files_complete([imported]):
            self.gguf = imported
            return imported
        original_paths = [os.path.join(root, os.path.basename(url)) for url in shards]
        if original_paths and self._model_files_complete(original_paths):
            self.gguf = original_paths[0]
            return self.gguf
        for i, url in enumerate(shards):
            fname = os.path.basename(url)
            # 保持原始分片文件名；llama.cpp 依赖该命名发现同组分片。
            dst = os.path.join(root, fname)
            dl.download_file(url, dst, sha256=m.get("sha256") if i == 0 else None)
            if not _valid_gguf(dst):
                raise RuntimeError(f"文字模型下载不完整：{fname}")
        if not self._model_files_complete(original_paths):
            raise RuntimeError("文字模型下载不完整或文件损坏，请重新下载后再分析")
        self.gguf = original_paths[0]
        return self.gguf

    def start(self) -> None:
        exe = self._ensure_binary()
        gguf = self._ensure_model()
        self.port = free_port()
        # Use the manifest's runtime hint so small, fully GPU-resident models do
        # not inherit the old 35-layer setting.  Keep a conservative context
        # window: the organizer sends compact chunk summaries, not the full
        # transcript, so this leaves room for KV cache on 6GB GPUs.
        runtime_params = str(self.model.get("params") or "-ngl 35").split()
        context = str(min(int(self.model.get("context_length") or 8192), 16384))
        args = [
            exe, "--model", gguf, "--host", "127.0.0.1", "--port", str(self.port),
            *runtime_params, "-c", context,
        ]
        LOG.info("启动 llama.cpp sidecar: port=%s model=%s", self.port, self.model["id"])
        self.proc = _spawn_local_server(args)
        _ACTIVE_PROCS.append(self.proc)
        startup_messages = deque(maxlen=20)
        stderr = self.proc.stderr

        def drain_stderr():
            # Keep a bounded in-memory tail; never persist classroom prompts.
            try:
                for line in iter(stderr.readline, b""):
                    startup_messages.append(line.decode("utf-8", errors="replace").strip())
            finally:
                stderr.close()

        threading.Thread(target=drain_stderr, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(120):
            if jobs_cancelled():
                self.stop()
                raise InterruptedError("已取消")
            if self.proc.poll() is not None:
                code = self.proc.returncode
                LOG.error("本地分析引擎退出 code=%s: %s", code, " | ".join(list(startup_messages)[-5:])[:1200])
                self.stop()
                raise RuntimeError(f"本地文字分析引擎启动失败（退出码 {code}）。已生成的录音转写已保留，可仅重新分析。")
            try:
                urllib.request.urlopen(self.base + "/health", timeout=2)
                return
            except Exception:
                time.sleep(1)
        self.stop()
        raise RuntimeError(f"llama.cpp 服务启动失败（端口 {self.port}）")

    def stop(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            try:
                _ACTIVE_PROCS.remove(self.proc)
            except ValueError:
                pass
            self.proc = None

    def available(self) -> bool:
        return self.is_binary_downloaded() and self.is_model_downloaded()

    def chat(self, system, user, json_mode=True, temperature=0.2) -> str:
        import httpx

        # Qwen3 enables its reasoning trace by default.  The organizer needs a
        # bounded JSON answer, not a long hidden chain-of-thought; explicitly
        # disable thinking for this local model so a re-analysis cannot appear
        # to hang while it fills the context window.
        user_content = user
        if self.model_id.lower().startswith("qwen3"):
            user_content = f"{user.rstrip()}\n/no_think"
        body = {
            "model": os.path.basename(self.gguf or self.model["id"]),
            "temperature": temperature,
            "max_tokens": 2048,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(self.base + "/v1/chat/completions", json=body, timeout=900)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def model_label(self) -> str:
        return f"Sidecar: {self.model['display']}"

    def close(self) -> None:
        self.stop()


# 启动期间用于检查取消（避免循环 import）
_CANCEL_CHECK = None

# 全局记录已启动的 sidecar 进程，便于应用退出时统一清理
_ACTIVE_PROCS: list = []


def set_cancel_check(fn) -> None:
    global _CANCEL_CHECK
    _CANCEL_CHECK = fn


def jobs_cancelled() -> bool:
    if _CANCEL_CHECK:
        return _CANCEL_CHECK()
    return False


def shutdown_all() -> None:
    """应用退出时终止所有仍运行的 llama.cpp sidecar 进程。"""
    for proc in list(_ACTIVE_PROCS):
        try:
            proc.terminate()
            proc.wait(5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _ACTIVE_PROCS.clear()
