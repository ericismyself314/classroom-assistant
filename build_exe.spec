# -*- mode: python ; coding: utf-8 -*-
"""课堂助手 PyInstaller 打包配置（Windows 单文件夹 exe）。

用法：
  pyinstaller build_exe.spec --noconfirm
  （**必须先 `rm -rf build dist`**，否则 COLLECT 阶段的批量删除会被系统保护拦住，
   导致构建在最后一步中断却没有任何报错。build_release.ps1 / build_exe.bat 已内置）

产物： dist/ClassroomAssistant/ClassroomAssistant.exe  （双击即可运行，无需安装 Python）

要点：
- 入口为 src/main.py（已把 src 加进路径并调用 backend.main）。
- 前端构建产物 frontend/dist 以「相对路径 frontend/dist」随包携带，
  与 api._resolve_dist_dir() 的查找逻辑对齐（exe 同级 / MEIPASS 解压根）。
- faster-whisper 体积较大，构建后会生成 GB 级产物，属正常。
- pywebview 走 Windows 默认的 edgechromium（WebView2）平台，
  用 collect_submodules / collect_data_files 把所有平台模块与本地资源一并打包。
"""
import os

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = os.path.dirname(os.path.abspath(SPEC))          # classroom-assistant/
SRC = os.path.join(ROOT, "src")

# faster-whisper/ctranslate2 需要 CUDA 12 与 cuDNN 9 的运行库。PyInstaller
# 不会自动收集这些 DLL；缺失时程序虽然能启动，但 auto 会静默退回 CPU。
# 这里复用本机已有且已经过真实识别验证的运行库，并在构建时明确报错。
local_appdata = os.environ.get("LOCALAPPDATA", "")
cuda_sources = [
    os.path.join(local_appdata, "ClassroomAssistant", "models", "llm", "bin"),
    os.path.join(local_appdata, "Programs", "Buzz", "_internal", "torch", "lib"),
]
cuda_names = [
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudart64_12.dll",
    "cudnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_engines_runtime_compiled64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_heuristic64_9.dll",
    "cudnn_ops64_9.dll",
]
cuda_binaries = []
missing_cuda = []
for name in cuda_names:
    source = next(
        (os.path.join(folder, name) for folder in cuda_sources
         if os.path.isfile(os.path.join(folder, name))),
        None,
    )
    if source:
        cuda_binaries.append((source, "."))
    else:
        missing_cuda.append(name)
assert not missing_cuda, (
    "缺少 GPU 识别运行库，继续构建会导致 auto 静默退回 CPU：\n  "
    + "\n  ".join(missing_cuda)
)

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
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "httpx",
    "pydantic",
    "webview",
    "webview.platforms",
    "webview.platforms.edgechromium",
    "webview.platforms.cef",
    "webview.platforms.winforms",
]
hiddenimports += collect_submodules("webview")
hiddenimports += collect_submodules("faster_whisper")
# 后端子模块（含新增的 selftest / ai.verification / ai.model_import 等惰性导入）
hiddenimports += collect_submodules("backend")

datas = [
    (os.path.join(ROOT, "frontend", "dist"), "frontend/dist"),
]
# pywebview 自带的原生资源（如 WebView2 loader 相关文件）
datas += collect_data_files("pywebview")
datas += collect_data_files("faster_whisper")

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=cuda_binaries,
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
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name="ClassroomAssistant",
)
