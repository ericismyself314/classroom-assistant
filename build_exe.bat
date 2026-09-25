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

REM 3) 检查前端构建产物
if not exist frontend\dist (
    echo [信息] 未找到 frontend\dist，开始构建前端 ...
    cd frontend
    call npm install
    call npm run build
    cd ..
) else (
    echo [3/5] 前端构建产物已存在 frontend\dist
)

REM 4) ⚠️ 打包前务必先清空 build/ 与 dist/
REM    PyInstaller COLLECT 阶段会批量删除它们；如不清空会被批量删除保护拦住，导致
REM    构建在最后一步中断却没有任何报错（看似成功实际未产出可用 exe）。
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist
echo [4/5] 已清理 build/ 与 dist/

REM 5) 执行打包
echo [5/5] 运行 PyInstaller（Qwen3-ASR 由独立本地 Python 服务承载）...
pyinstaller build_exe.spec --noconfirm

REM 实际产物名以 build_exe.spec 为准（COLLECT name=ClassroomAssistant）
if exist "dist\ClassroomAssistant\ClassroomAssistant.exe" (
    echo.
    echo 构建成功！产物位于：
    echo   dist\ClassroomAssistant\ClassroomAssistant.exe
    echo 双击即可运行（需本机已安装 WebView2 运行时，Windows 11 自带）。
    echo.
    echo 接下来可以用 installer\ClassroomAssistant.iss（Inno Setup）或
    echo installer\ClassroomAssistant.nsi（NSIS）打成安装包，或直接交付此目录。
) else (
    echo.
    echo [失败] 未生成 dist\ClassroomAssistant\ClassroomAssistant.exe，请查看上方报错。
    exit /b 1
)

endlocal
