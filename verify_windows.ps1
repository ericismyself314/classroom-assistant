# 课堂助手 一键环境自检（PowerShell 版）
# 用法：右键“使用 PowerShell 运行”，或 powershell -ExecutionPolicy Bypass -File verify_windows.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" }
      elseif (Test-Path "..\venv\Scripts\python.exe") { "..\venv\Scripts\python.exe" }
      else { "python" }

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "   课堂助手 一键环境自检 (verify_windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

& $py run.py --self-test
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[失败] 自检未通过。请根据上方报告处理：" -ForegroundColor Red
    Write-Host "  - 模型未下载：在设置页点“下载”，或“手动导入”已下好的模型。"
    Write-Host "  - 目录不可写 / 数据库失败：以管理员身份运行，或检查杀毒软件。"
    Write-Host "  - 前端产物缺失：frontend 目录执行 npm run build。"
    Read-Host "按回车退出"
    exit 1
}

Write-Host ""
Write-Host "[通过] 环境就绪。双击 run.py（或 classroom-assistant.exe）即可启动。" -ForegroundColor Green
Read-Host "按回车退出"
