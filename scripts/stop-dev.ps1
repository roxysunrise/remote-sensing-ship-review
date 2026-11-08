<#
.SYNOPSIS
    停止 `scripts/start-dev.ps1` 起的后端与前端进程。

.EXAMPLE
    pwsh scripts/stop-dev.ps1
    pwsh scripts/stop-dev.ps1 -KeepLogs
#>
[CmdletBinding()]
param([switch]$KeepLogs)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $Root "var\dev-pids.json"

if (-not (Test-Path $pidFile)) {
    Write-Host "没有 var\dev-pids.json，说明不是 start-dev.ps1 起的（或已清理）" -ForegroundColor Yellow
} else {
    $pids = Get-Content $pidFile -Raw | ConvertFrom-Json
    foreach ($name in @("backend", "web")) {
        $procId = $pids.$name
        if (-not $procId) { continue }
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "已停止 $name (PID $procId)" -ForegroundColor Green
        } else {
            Write-Host "$name (PID $procId) 已不在运行" -ForegroundColor DarkGray
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 兜底：按脚本路径清理漏网的 server / serve.py
foreach ($pattern in @("backend.server", "serve.py")) {
    Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pattern*" -and $_.CommandLine -like "*$Root*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "已清理残留 $pattern (PID $($_.ProcessId))" -ForegroundColor DarkGray
        }
}

if (-not $KeepLogs) {
    Write-Host "日志保留在 var\logs\（未删除）" -ForegroundColor DarkGray
}
