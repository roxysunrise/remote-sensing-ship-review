<#
.SYNOPSIS
    一条命令把「遥感船舶检测与复核」跑起来：后端 + 前端（同源反代）+ 浏览器。

.DESCRIPTION
    1. 检查 Python 与依赖（fastapi / uvicorn / ultralytics）；
    2. 起后端 `python -m backend.server`（默认 127.0.0.1:8000）；
    3. 起 `scripts/serve.py`：静态页面 + /api/v1 反代，前端零配置、无 CORS（默认 127.0.0.1:8765）；
    4. 轮询 /health，就绪后打开浏览器；
    5. PID 写入 `var/dev-pids.json`，用 `scripts/stop-dev.ps1` 停。

.EXAMPLE
    pwsh scripts/start-dev.ps1
    pwsh scripts/start-dev.ps1 -ApiPort 8012 -WebPort 8766 -Weights models\_local\yolov8s-obb.pt
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 8765,
    [string]$Python = "",
    [string]$Weights = "",
    [string]$Frontend = "",
    [string]$Listen = "127.0.0.1",
    [switch]$NoBrowser,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "ERR $text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- Python
if (-not $Python) {
    $candidates = @(
        $env:SHIP_PYTHON,
        "E:\conda\envs\obb\python.exe",
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    ) | Where-Object { $_ -and (Test-Path $_) }
    if (-not $candidates) { Fail "找不到 Python，请用 -Python 指定解释器路径" }
    $Python = $candidates[0]
}
$Python = (Resolve-Path $Python).Path
Write-Step "Python: $Python"
$version = & $Python -c "import sys;print('.'.join(map(str,sys.version_info[:3])))" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "这个解释器不可用：$Python" }
Write-Ok "版本 $version"

# ---------------------------------------------------------------- 依赖
Write-Step "检查依赖"
$probe = & $Python -c "import importlib.util as u;mods={'fastapi':'fastapi','uvicorn':'uvicorn','torch':'torch','ultralytics':'ultralytics','PIL':'Pillow','cv2':'opencv-python'};print(','.join(p for m,p in mods.items() if u.find_spec(m) is None))" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "依赖探测失败" }
if ($probe) {
    Write-Warn "缺少：$probe"
    Write-Host "    安装：& `"$Python`" -m pip install -r algorithm/requirements.txt -r backend/requirements.txt" -ForegroundColor Yellow
    Fail "依赖不全，先装依赖再启动"
}
Write-Ok "fastapi / uvicorn / torch / ultralytics / Pillow / opencv 都在"

# ---------------------------------------------------------------- 权重
if (-not $Weights) {
    foreach ($candidate in @($env:SHIP_WEIGHTS,
                            "models\yolov8l-obb-dota-v1-80e\best.pt",
                            "weights\dota_v1_yolov8l_80e\weights\best.pt",
                            "models\_local\yolov8s-obb.pt")) {
        if ($candidate -and (Test-Path $candidate)) { $Weights = $candidate; break }
    }
}
if ($Weights -and (Test-Path $Weights)) {
    $Weights = (Resolve-Path $Weights).Path
    Write-Step "权重: $Weights"
    if ($Weights -notmatch "yolov8l-obb-dota-v1-80e|dota_v1_yolov8l_80e") {
        Write-Warn "这不是项目训练权重最佳（models/yolov8l-obb-dota-v1-80e/best.pt），演示链路可用但精度不代表项目结果"
    }
} else {
    $Weights = ""
    Write-Warn "没有找到权重：服务能起，但检测任务会以 MODEL_NOT_FOUND 失败"
    Write-Warn "把 best.pt 放到 models\yolov8l-obb-dota-v1-80e\，或用 -Weights 指定"
}

# ---------------------------------------------------------------- 前端目录
if (-not $Frontend) {
    foreach ($candidate in @("frontend", "..\_wt_frontend\frontend", "..\remote-sensing-ship-review\frontend")) {
        if ($candidate -and (Test-Path (Join-Path $candidate "login.html"))) { $Frontend = $candidate; break }
    }
}
if (-not $Frontend -or -not (Test-Path (Join-Path $Frontend "login.html"))) {
    Fail "找不到前端目录（需要含 login.html）。用 -Frontend 指定，例如 -Frontend ..\_wt_frontend\frontend"
}
$Frontend = (Resolve-Path $Frontend).Path
Write-Step "前端: $Frontend"

# ---------------------------------------------------------------- 端口
foreach ($pair in @(@{Port=$ApiPort;Name="后端"}, @{Port=$WebPort;Name="前端"})) {
    $busy = Get-NetTCPConnection -LocalPort $pair.Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        if ($Force) {
            $busy | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
            Write-Warn "已释放占用 $($pair.Port) 的进程（-Force）"
        } else {
            Fail "$($pair.Name)端口 $($pair.Port) 已被占用，换端口或加 -Force"
        }
    }
}

# ---------------------------------------------------------------- 启动
$logDir = Join-Path $Root "var\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$env:SHIP_DEVICE = if ($env:SHIP_DEVICE) { $env:SHIP_DEVICE } else { "cpu" }
$env:SHIP_PRELOAD = if ($env:SHIP_PRELOAD) { $env:SHIP_PRELOAD } else { "1" }
if ($Weights) { $env:SHIP_WEIGHTS = $Weights }

Write-Step "启动后端 (:$ApiPort)"
$backendArgs = @("-m", "backend.server", "--host", $Listen, "--port", "$ApiPort")
$backend = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $logDir "backend.out.log") `
    -RedirectStandardError (Join-Path $logDir "backend.err.log") `
    -WindowStyle Hidden -PassThru
Write-Ok "PID $($backend.Id)  日志 var\logs\backend.*.log"

Write-Step "启动前端 (:$WebPort，同源反代 /api/v1 → :$ApiPort)"
$webArgs = @("scripts\serve.py", "--frontend", "`"$Frontend`"", "--port", "$WebPort", "--backend", "http://127.0.0.1:$ApiPort")
$web = Start-Process -FilePath $Python -ArgumentList $webArgs -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $logDir "web.out.log") `
    -RedirectStandardError (Join-Path $logDir "web.err.log") `
    -WindowStyle Hidden -PassThru
Write-Ok "PID $($web.Id)  日志 var\logs\web.*.log"

# ---------------------------------------------------------------- 就绪
Write-Step "等待后端就绪"
$health = "http://$Listen`:$ApiPort/api/v1/health"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $body = Invoke-RestMethod -Uri $health -TimeoutSec 3
        if ($body.status) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 700 }
}
if (-not $ready) {
    Write-Warn "后端 60s 内没起来，看 var\logs\backend.err.log"
} else {
    $modelState = if ($body.model_ready) { "模型已加载" } else { "模型未加载（首个任务会加载）" }
    Write-Ok "health: $($body.status) · $modelState · active_tasks=$($body.active_tasks)"
}

$webReady = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://$Listen`:$WebPort/login.html" -TimeoutSec 3 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $webReady = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if ($webReady) { Write-Ok "前端可访问" } else { Write-Warn "前端没起来，看 var\logs\web.err.log" }

# ---------------------------------------------------------------- 记录 PID
$pidFile = Join-Path $Root "var\dev-pids.json"
@{ backend = $backend.Id; web = $web.Id; api_port = $ApiPort; web_port = $WebPort;
   started_at = (Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -Path $pidFile -Encoding utf8

$url = "http://$Listen`:$WebPort/login.html"
Write-Host ""
Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host " 打开： $url" -ForegroundColor Green
Write-Host " 账号： roxysunrise / demo-1234 / harbor-a（项目负责人）"
Write-Host "       analyst     / demo-1234 / harbor-a（项目分析员）"
Write-Host " 停止： pwsh scripts/stop-dev.ps1"
Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray

if (-not $NoBrowser) { Start-Process $url }
