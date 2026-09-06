@echo off
setlocal
cd /d "%~dp0"

REM 优先使用项目自带虚拟环境，其次上级目录 venv，最后回退系统 python
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else if exist "..\\venv\Scripts\python.exe" (
    set "PY=..\\venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo ================================================
echo    课堂助手 一键环境自检 (verify_windows)
echo ================================================
echo.

"%PY%" run.py --self-test
if errorlevel 1 (
    echo.
    echo [失败] 自检未通过。请根据上方报告处理：
    echo   - 模型未下载：打开应用后在“设置”页点“下载”，或“手动导入”已下好的模型。
    echo   - 目录不可写 / 数据库失败：尝试以管理员身份运行，或检查杀毒软件是否拦截。
    echo   - 前端产物缺失：在 frontend 目录执行 npm run build 重新生成。
    echo.
    pause
    exit /b 1
)

echo.
echo [通过] 环境就绪。双击 run.py（或 classroom-assistant.exe）即可启动。
echo.
pause
