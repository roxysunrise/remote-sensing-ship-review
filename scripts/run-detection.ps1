param(
    [string]$Image = "datasets/smoke/dense.png",
    [string]$Output = "outputs/inference",
    [string]$Device = "cpu",
    [double]$Conf = 0.25,
    [switch]$Tiled,
    [int]$TileSize = 1024,
    [int]$Stride = 512,
    [double]$MergeIou = 0.1
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
    $detectionArgs = @("-m", "algorithm.ship_detector", $Image, "--output", $Output, "--device", $Device,
        "--conf", $Conf.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--tile-size", $TileSize, "--stride", $Stride,
        "--merge-iou", $MergeIou.ToString([System.Globalization.CultureInfo]::InvariantCulture))
    if ($Tiled) { $detectionArgs += "--tiled" }
    & $pythonPath @detectionArgs
    $inferenceExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $inferenceExit
