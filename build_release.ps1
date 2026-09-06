# build_release.ps1 — 课堂助手 Windows 最终交付一键构建脚本（阶段十六）
#
# 用途：在真实 Windows 上，从干净状态生成可交付安装包。
# 流程（任一环节失败即终止）：clean → 依赖 → 前端 typecheck/build → 后端测试 →
#       安全自检 → PyInstaller onedir → 打包自检 → 安装包 → SHA256 → 报告。
#
# 前置（需用户自行准备）：
#   - Python 3.13（含 venv），脚本默认用项目同级 ../venv/Scripts/python.exe
#   - Node 22+ 与 npm
#   - 安装 Inno Setup 6（iscc.exe）以生成 ClassroomAssistant-Setup-x64.exe；
#     若仅有 NSIS，可改用 ClassroomAssistant.nsi（见下方 $UseNsis 开关）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File build_release.ps1
#   powershell -ExecutionPolicy Bypass -File build_release.ps1 -UseNsis

[CmdletBinding()]
param(
    [switch]$UseNsis,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$FRONTEND = Join-Path $ROOT "frontend"
$BACKEND = Join-Path $ROOT "src"
$INSTALLER = Join-Path $ROOT "installer"
$REPORT_PATH = Join-Path $ROOT "RELEASE_REPORT.md"

# 可覆盖的解释器路径
$PYTHON = "python"
if ($PythonPath) {
    $PYTHON = $PythonPath
} elseif (Test-Path (Join-Path $ROOT "..\venv\Scripts\python.exe")) {
    $PYTHON = (Resolve-Path (Join-Path $ROOT "..\venv\Scripts\python.exe")).Path
}
$NODE = "node"
$NPM = "npm"
$env:PYTHONUTF8 = "1"

function Fail($msg) {
    Write-Host "❌ $msg" -ForegroundColor Red
    exit 1
}
function Step($msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

# 先确认所有外部工具存在，再删除上一份已验证产物。
Step "发布环境预检查（不改动现有产物）"
if ($PYTHON -eq "python" -and -not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "未找到 Python 3.13" }
if (-not (Get-Command $NODE -ErrorAction SilentlyContinue)) { Fail "未找到 Node.js" }
if (-not (Get-Command $NPM -ErrorAction SilentlyContinue)) { Fail "未找到 npm" }
if ($UseNsis) {
    $compilerCommand = Get-Command makensis.exe -ErrorAction SilentlyContinue
    $COMPILER = if ($compilerCommand) { $compilerCommand.Source } else { $null }
    if (-not $COMPILER) { Fail "未找到 NSIS makensis.exe；现有 dist 尚未清理" }
} else {
    $compilerCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
    $COMPILER = if ($compilerCommand) { $compilerCommand.Source } else {
        $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
        @(
            (Join-Path $pf86 "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    }
    if (-not $COMPILER) { Fail "未找到 Inno Setup 6 的 ISCC.exe；现有 dist 尚未清理" }
}
Write-Host "✅ 发布工具齐全"

# 1) 清理
Step "1/9 清理旧产物"
Remove-Item -Recurse -Force (Join-Path $ROOT "dist") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ROOT "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ROOT "frontend\dist") -ErrorAction SilentlyContinue

# 2) 依赖
Step "2/9 安装/校验依赖"
& $PYTHON -m pip install --quiet --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "pip 升级失败" }
& $PYTHON -m pip install --quiet -r (Join-Path $ROOT "requirements.txt") 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Python 依赖安装失败" }
Push-Location $FRONTEND
& $NPM ci
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "前端依赖安装失败" }
Pop-Location

# 3) 前端 typecheck + build
Step "3/9 前端 typecheck + build"
Push-Location $FRONTEND
& $NPM run typecheck; if ($LASTEXITCODE -ne 0) { Fail "前端 typecheck 失败" }
& $NPM run build;     if ($LASTEXITCODE -ne 0) { Fail "前端 build 失败" }
Pop-Location

# 4) 后端测试
Step "4/9 后端单元测试"
& $PYTHON -m pytest (Join-Path $ROOT "tests") -q; if ($LASTEXITCODE -ne 0) { Fail "后端测试未通过" }

# 5) 安全自检（禁止把密钥/外部依赖写死进包）
Step "5/9 安全自检"
$leakPatterns = @('sk-[A-Za-z0-9]{20,}', 'api_key\s*=\s*[A-Za-z0-9]{20,}')
$leaks = Get-ChildItem -Path $BACKEND -Recurse -Filter "*.py" -File |
    Select-String -Pattern $leakPatterns -ErrorAction SilentlyContinue
if ($leaks) { Fail "发现疑似硬编码密钥，已中止：$($leaks.Count) 处" }
Write-Host "✅ 未发现硬编码密钥"

# 6) PyInstaller onedir
Step "6/9 PyInstaller 打包（onedir）"
& $PYTHON -m PyInstaller (Join-Path $ROOT "build_exe.spec") --noconfirm; if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 构建失败" }

# 7) 打包自检
Step "7/9 打包后自检（--self-test 必须 0；--readiness-check 必须 10）"
$EXE = Join-Path $ROOT "dist\ClassroomAssistant\ClassroomAssistant.exe"
if (-not (Test-Path $EXE)) { Fail "未生成 $EXE" }
# post_package_check 内部使用独立 LOCALAPPDATA，并包含上述两项 CLI 检查。
& $PYTHON (Join-Path $ROOT "tests\post_package_check.py") $EXE
if ($LASTEXITCODE -ne 0) { Fail "打包后 API/前端冒烟测试未通过" }
Write-Host "✅ 隔离自检与 API/前端冒烟测试通过"

# 8) 安装包
Step "8/9 生成安装包"
$SETUP = Join-Path $ROOT "installer\ClassroomAssistant-Setup-x64.exe"
if ($UseNsis) {
    $nsi = Join-Path $INSTALLER "ClassroomAssistant.nsi"
    & $COMPILER "$nsi"; if ($LASTEXITCODE -ne 0) { Fail "NSIS 构建失败" }
} else {
    $iss = Join-Path $INSTALLER "ClassroomAssistant.iss"
    & $COMPILER "$iss"; if ($LASTEXITCODE -ne 0) { Fail "Inno Setup 构建失败" }
}
if (-not (Test-Path $SETUP)) { Fail "未生成安装包 $SETUP" }
Write-Host "✅ 安装包已生成"

# 9) SHA256 + 报告
Step "9/9 计算 SHA256 并生成报告"
$hash = (Get-FileHash $SETUP -Algorithm SHA256).Hash
$sizeMB = [math]::Round((Get-Item $SETUP).Length / 1MB, 1)
$setupLabel = "installer\ClassroomAssistant-Setup-x64.exe"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$reportContent = @"
# 课堂助手 发布报告

- 生成时间：$ts
- 安装包：$setupLabel
- 大小：$sizeMB MB
- SHA256：$hash
- 运行要求：Windows 10/11 64 位；需 Microsoft Edge WebView2 运行时（系统自带 Edge 通常已包含）。
- 模型：首次使用在首启向导或「设置」点击“一键准备推荐模型”（约数百 MB~数 GB），处理完全在本机进行。
- 数据位置：%LOCALAPPDATA%\ClassroomAssistant（与安装目录分离，卸载保留数据）。

## 验证结论
- 前端 typecheck / build：通过
- 后端测试：通过
- 安全自检：未发现硬编码密钥
- 打包 --self-test：EXIT=0
- 打包 --readiness-check：EXIT=10（应用可启动，模型缺失）
- 安装包：已生成

## 已知限制（沙箱无法验证，需在真实 Windows 复核）
- 真实录音转写 + 本地大模型分析需要在目标机准备模型后再做一次验收。
- 干净 Windows 的双击安装体验需人工复核（安装器默认创建桌面快捷方式）。
"@
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($REPORT_PATH, $reportContent, $utf8Bom)
Write-Host "✅ 报告已写入 $REPORT_PATH"
Write-Host "`n🎉 交付构建完成。安装包：$SETUP"
Write-Host "   SHA256: $hash"
