param(
    [string]$Image = "datasets/smoke/dense.png",
    [string]$Output = "outputs/inference",
    [string]$Device = "cpu",
    [double]$Conf = 0.25
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing .venv. Follow algorithm/README.md to install dependencies."
}
Push-Location $projectRoot
try {
    $env:PYTHONIOENCODING = "utf-8"
    & $pythonPath -m algorithm.ship_detector $Image --output $Output --device $Device --conf $Conf
    $inferenceExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $inferenceExit
