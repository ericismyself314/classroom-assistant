# -*- mode: python ; coding: utf-8 -*-
"""课堂助手 PyInstaller 打包配置（Windows 单文件夹 exe）。

用法：
  pyinstaller build_exe.spec --noconfirm --clean --distpath <临时输出目录> --workpath <临时工作目录>

产物： <输出目录>/ClassroomAssistant/ClassroomAssistant.exe（便携 GUI 无需 Python；Qwen ASR 需要本地 Python 3.12 环境）

要点：
- 入口为 src/main.py（已把 src 加进路径并调用 backend.main）。
- 前端构建产物 frontend/dist 以「相对路径 frontend/dist」随包携带，
  与 api._resolve_dist_dir() 的查找逻辑对齐（exe 同级 / MEIPASS 解压根）。
- Qwen3-ASR/PyTorch 由独立本地 Python 进程承载，不重复打进 GUI。
- pywebview 走 Windows 默认的 edgechromium（WebView2）平台，
  用 collect_submodules / collect_data_files 把所有平台模块与本地资源一并打包。
"""
import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = os.path.dirname(os.path.abspath(SPEC))          # classroom-assistant/
SRC = os.path.join(ROOT, "src")

# Qwen/PyTorch 只存在于独立语音进程；GUI 不再重复携带 CUDA DLL。
# 前端构建产物必须存在
dist_dir = os.path.join(ROOT, "frontend", "dist")
assert os.path.isdir(dist_dir), (
    "未找到 frontend/dist，请先构建前端：\n"
    "  cd frontend && npm install && npm run build"
)

block_cipher = None

hiddenimports = [
    "backend",
    "backend.ai",
    "backend.ai.llm_provider",
    "backend.ai.manifest",
    "backend.ai.prompts",
    "backend.ai.provider",
    "backend.ai.segment_analysis",
    "backend.ai.whisper_worker",
    "httpx",
    "pydantic",
    "webview",
    "webview.platforms",
    "webview.platforms.edgechromium",
    "webview.platforms.cef",
    "webview.platforms.winforms",
]
hiddenimports += collect_submodules("webview")
# 后端子模块（含新增的 selftest / ai.verification / ai.model_import 等惰性导入）
hiddenimports += collect_submodules("backend")

datas = [
    (os.path.join(ROOT, "frontend", "dist"), "frontend/dist"),
]
# pywebview 自带的原生资源（如 WebView2 loader 相关文件）
datas += collect_data_files("pywebview")
datas.append((os.path.join(SRC, "backend", "ai", "qwen_worker_service.py"), "backend/ai"))

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "unittest",
        "test",
        "torch",
        "qwen_asr",
        "faster_whisper",
        "ctranslate2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClassroomAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # PyTorch/CUDA DLLs are not safe to UPX-compress on all Windows builds;
    # keep the native runtime uncompressed to prevent 0xc0000409 at startup.
    upx=False,
    console=False,                 # 双击运行不弹黑窗口（GUI）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="课堂助手.ico",           # 应用与桌面快捷方式使用统一图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClassroomAssistant",
)
