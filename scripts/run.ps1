$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "No virtual environment found at .venv" -ForegroundColor Red
    Write-Host ""
    Write-Host "Create it first (one command, handles the Python version for you):" -ForegroundColor Yellow
    Write-Host "  .\scripts\setup.ps1"
    Write-Host ""
    Write-Host "Manual alternative:" -ForegroundColor Yellow
    Write-Host "  py -3.12 -m venv .venv"
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host "  pip install -e `".[cpu]`""
    Write-Host ""
    Write-Host "Full guide: docs/en/getting-started.md or docs/zh-CN/getting-started.md"
    exit 1
}

& $Python -c "import annoweave" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The .venv exists but AnnoWeave is not installed in it." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix it with:" -ForegroundColor Yellow
    Write-Host "  .\scripts\setup.ps1"
    Write-Host "or, if you prefer to keep the current environment:" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe -m pip install -e `".[cpu]`""
    exit 1
}

& $Python -m annoweave
exit $LASTEXITCODE
