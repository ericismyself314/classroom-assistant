@echo off
chcp 65001 >nul
setlocal
cd /d %~dp0

echo ============================================
echo   课堂助手  Windows 打包脚本
echo ============================================

REM 1) 准备独立虚拟环境（避免污染系统 Python）
if not exist venv (
    echo [1/5] 创建虚拟环境 venv ...
    python -m venv venv
    call venv\Scripts\activate
    python -m pip install --upgrade pip
) else (
    call venv\Scripts\activate
)

REM 2) 安装依赖
echo [2/5] 安装 Python 依赖（requirements.txt + pyinstaller）...
pip install -r requirements.txt
pip install pyinstaller

REM 3) 每次从锁文件恢复依赖并重建前端，避免把旧 dist 打进新 exe。
echo [3/5] 根据 package-lock.json 安装前端依赖并构建 ...
pushd frontend
call npm ci
if errorlevel 1 (
    popd
    exit /b 1
)
call npm run build
if errorlevel 1 (
    popd
    exit /b 1
)
popd

REM 4) 使用独立临时目录，保留项目中现有的 build/ 与 dist/ 文件。
set "BUILD_ROOT=%TEMP%\ClassroomAssistant-build-%RANDOM%-%RANDOM%"
if exist "%BUILD_ROOT%" (
    echo [失败] 临时构建目录已存在：%BUILD_ROOT%
    exit /b 1
)
mkdir "%BUILD_ROOT%"
if errorlevel 1 exit /b 1
echo [4/5] 构建输出目录：%BUILD_ROOT%

REM 5) 执行打包
echo [5/5] 运行 PyInstaller（Qwen3-ASR 由独立本地 Python 服务承载）...
pyinstaller build_exe.spec --noconfirm --clean --distpath "%BUILD_ROOT%\dist" --workpath "%BUILD_ROOT%\work"

REM 实际产物名以 build_exe.spec 为准（COLLECT name=ClassroomAssistant）
if exist "%BUILD_ROOT%\dist\ClassroomAssistant\ClassroomAssistant.exe" (
    echo.
    echo 构建成功！产物位于：
    echo   %BUILD_ROOT%\dist\ClassroomAssistant\ClassroomAssistant.exe
    echo 双击即可运行（需 WebView2；语音识别还需本地 Python 3.12 + qwen-asr）。
    echo.
    echo 发布时请压缩整个 ClassroomAssistant 文件夹，不要只分发 exe。
) else (
    echo.
    echo [失败] 未生成 %BUILD_ROOT%\dist\ClassroomAssistant\ClassroomAssistant.exe，请查看上方报错。
    exit /b 1
)

endlocal
