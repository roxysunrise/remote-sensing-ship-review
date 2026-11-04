param(
    [string]$Bind = "127.0.0.1",
    [int]$Port = 8000,
    [string]$Device = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing .venv. Follow algorithm/README.md and backend/README.md to install dependencies."
}
Push-Location $projectRoot
try {
    $env:PYTHONIOENCODING = "utf-8"
    if ($Device -ne "") { $env:SHIP_DEVICE = $Device }
    & $pythonPath -m backend.server --host $Bind --port $Port
    $backendExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $backendExit
