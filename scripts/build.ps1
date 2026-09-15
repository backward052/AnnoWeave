[CmdletBinding()]
param(
    [ValidateSet("cpu", "gpu")]
    [string]$Runtime = "cpu",
    [switch]$SkipInstall,
    [switch]$SkipTests
)

# Builds dist\AnnoWeave\AnnoWeave.exe with PyInstaller.
#
#   .\scripts\build.ps1 -Runtime cpu
#
# The virtual environment is created by scripts/setup.ps1. This script never
# hard-codes a Python minor version: it reuses .venv when present and otherwise
# falls back to whatever 64-bit CPython the machine provides.

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$sourcePython = "the system Python"

# A running AnnoWeave.exe holds a file lock on the previous build output. PyInstaller
# then dies with a bare "PermissionError: [WinError 5] Access is denied", which gives
# no hint about the cause, so catch it up front.
$running = @(Get-Process -Name AnnoWeave -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Host "AnnoWeave is still running, which locks the previous build output:" -ForegroundColor Red
    $running | ForEach-Object { Write-Host "  pid $($_.Id) started $($_.StartTime)" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "Close the application window, or stop it with:" -ForegroundColor Yellow
    Write-Host "  Stop-Process -Name AnnoWeave" -ForegroundColor White
    Write-Host ""
    Write-Host "Then run this script again." -ForegroundColor Yellow
    exit 1
}

if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
    $sourcePython = ".venv"
} else {
    Write-Host "No .venv found. Creating one with scripts/setup.ps1..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup.ps1") -Runtime $Runtime -SkipTests
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "setup.ps1 did not create .venv. Run .\scripts\setup.ps1 manually and read its output."
    }
    $Python = $VenvPython
    $sourcePython = ".venv (created by setup.ps1)"
}

if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -e "$ProjectRoot[$Runtime,build]"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Run .\scripts\setup.ps1 -Runtime $Runtime first and check its output."
    }
}

if (-not $SkipTests) {
    $previousPlatform = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    try {
        & $Python -m pytest
    } finally {
        if ($null -eq $previousPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        else { $env:QT_QPA_PLATFORM = $previousPlatform }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed; not building. Re-run with -SkipTests only if you understand the failures."
    }
}

Write-Host "Building with $sourcePython" -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $ProjectRoot "dist") `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    (Join-Path $ProjectRoot "packaging\pyinstaller\annoweave.spec")
if ($LASTEXITCODE -ne 0) {
    $stillRunning = @(Get-Process -Name AnnoWeave -ErrorAction SilentlyContinue)
    if ($stillRunning.Count -gt 0) {
        throw "PyInstaller failed while AnnoWeave is running (pid $($stillRunning.Id -join ', ')). Close the app, then run: Stop-Process -Name AnnoWeave"
    }
    throw "PyInstaller failed. See docs/en/building-windows.md for the Qt and ONNX Runtime troubleshooting list."
}

$Executable = Join-Path $ProjectRoot "dist\AnnoWeave\AnnoWeave.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Build finished without the expected executable: $Executable"
}

# PyInstaller leaves a second, non-runnable AnnoWeave.exe in the work directory.
# It has no _internal\pythonXXX.dll next to it, so launching it fails with
# "Failed to load Python DLL". Delete it so exactly one AnnoWeave.exe exists and
# nobody can pick the wrong one.
$DecoyExecutables = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "build") -Recurse -Filter "AnnoWeave.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -ne $Executable }
)
foreach ($decoy in $DecoyExecutables) {
    Remove-Item -LiteralPath $decoy.FullName -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Built: $Executable" -ForegroundColor Green
if ($DecoyExecutables.Count -gt 0) {
    Write-Host "Removed $($DecoyExecutables.Count) non-runnable copy from build\ (it lacked the Python DLL)." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Run it with:" -ForegroundColor White
Write-Host "  & `"$Executable`""
Write-Host "Or double-click AnnoWeave.exe inside dist\AnnoWeave\ in Explorer." -ForegroundColor White
Write-Host "Distribute the whole dist\AnnoWeave directory, not the .exe alone."
