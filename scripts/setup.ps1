[CmdletBinding()]
param(
    [ValidateSet("cpu", "gpu")]
    [string]$Runtime = "cpu",
    [string]$Python = "",
    [ValidateRange(0, 20)]
    [int]$MinimumMinor = 3,
    [switch]$Recreate,
    [switch]$SkipInstall,
    [switch]$SkipTests
)

# Creates .venv, installs AnnoWeave, and verifies the result.
#
#   .\scripts\setup.ps1                 # CPU runtime, recommended path
#   .\scripts\setup.ps1 -Runtime gpu    # NVIDIA CUDA runtime
#   .\scripts\setup.ps1 -Recreate       # rebuild .venv from scratch
#
# Every failure prints the exact command to run next, so you never have to guess.

$ErrorActionPreference = "Stop"
$script:ProblemCount = 0
$script:SupportedMinors = @(13, 12, 11, 10)

function Write-Step { param([string]$Message) Write-Host ""; Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "    OK: $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "    !  $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "    X  $Message" -ForegroundColor Red; $script:ProblemCount++ }

function ConvertTo-ArgumentString {
    # Minimal quoting good enough for `-3.12 -c "..."` style argument vectors on
    # Windows PowerShell 5.1, which has no ProcessStartInfo.ArgumentList.
    param([string[]]$Arguments)
    $quoted = foreach ($argument in $Arguments) {
        if ($argument -match '[\s"]' -and -not $argument.Contains("'")) {
            "'$argument'"
        } else {
            '"' + $argument.Replace('"', '\"') + '"'
        }
    }
    return ($quoted -join ' ')
}

function Invoke-Quiet {
    # Runs a native command in an isolated process and returns its stdout, or
    # $null when it fails. A separate process is required because Windows
    # PowerShell turns native stderr into a terminating error while
    # $ErrorActionPreference is 'Stop' - which is exactly what `py -3.12` emits
    # on a machine that has no 3.12 installed.
    param([string]$Command, [string[]]$Arguments = @())
    try {
        $shell = if (Get-Command $Command -CommandType Application -ErrorAction SilentlyContinue) {
            $Command
        } else {
            (Get-Command $Command -ErrorAction Stop).Source
        }
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $shell
        $startInfo.Arguments = ConvertTo-ArgumentString $Arguments
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $process = [System.Diagnostics.Process]::Start($startInfo)
        $out = $process.StandardOutput.ReadToEnd()
        $null = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { return $null }
        if (-not $out) { return "" }
        return $out.Trim()
    } catch {
        return $null
    }
}

function Get-InterpreterInfo {
    # Returns version/architecture for a resolved interpreter path, or $null.
    param([string]$Executable)
    if (-not $Executable) { return $null }
    $resolved = $Executable
    if (-not (Test-Path -LiteralPath $resolved)) {
        $command = Get-Command $resolved -ErrorAction SilentlyContinue
        if (-not $command) { return $null }
        $resolved = $command.Source
    }
    $text = Invoke-Quiet -Command $resolved -Arguments @(
        "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    )
    if (-not $text) { return $null }
    $bits = Invoke-Quiet -Command $resolved -Arguments @(
        "-c", "import struct; print(struct.calcsize('P') * 8)"
    )
    $parts = $text -split '\.'
    return [pscustomobject]@{
        Path  = $resolved
        Text  = $text
        Minor = [int]$parts[1]
        Bits  = $bits
    }
}

function Get-LauncherInfo {
    # Probes `py -3.<minor>` and returns version info, or $null when absent.
    param([int]$Minor)
    $text = Invoke-Quiet -Command "py" -Arguments @(
        "-3.$Minor", "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    )
    if (-not $text) { return $null }
    $bits = Invoke-Quiet -Command "py" -Arguments @(
        "-3.$Minor", "-c", "import struct; print(struct.calcsize('P') * 8)"
    )
    return [pscustomobject]@{
        Spec  = "-3.$Minor"
        Path  = "py -3.$Minor"
        Text  = $text
        Minor = $Minor
        Bits  = $bits
    }
}

function Find-CompatiblePython {
    param([string]$Requested, [int]$MinimumMinor)

    if ($Requested) {
        $info = Get-InterpreterInfo -Executable $Requested
        if (-not $info) {
            throw "Python '$Requested' could not be run. Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/ and tick 'Add python.exe to PATH', or pass -Python with a full path to python.exe."
        }
        return $info
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = @()
        foreach ($minor in $script:SupportedMinors) {
            if ($minor -lt $MinimumMinor) { continue }
            $info = Get-LauncherInfo -Minor $minor
            if ($info) { $found += $info }
        }
        if ($found.Count -gt 0) {
            # Prefer 3.13/3.12: current, and covered by the CI matrix.
            $preferred = $found | Where-Object { $_.Minor -in @(13, 12) } | Select-Object -First 1
            if (-not $preferred) { $preferred = $found | Select-Object -First 1 }
            return $preferred
        }
    }

    foreach ($name in @("python3", "python")) {
        $info = Get-InterpreterInfo -Executable $name
        if ($info -and $info.Minor -ge $MinimumMinor -and $info.Minor -le 13) { return $info }
    }

    throw @"
No supported Python was found. AnnoWeave needs 64-bit Python 3.$MinimumMinor - 3.13 (3.12 or 3.13 recommended).

Install one of these, then re-run this script:
  1. winget:          winget install Python.Python.3.12
  2. python.org:      https://www.python.org/downloads/windows/   (tick "Add python.exe to PATH")
  3. Microsoft Store: search for "Python 3.12"

Already installed but not detected? Pass the interpreter explicitly:
  .\scripts\setup.ps1 -Python "C:\Path\To\python.exe"
"@
}

# ---------------------------------------------------------------- main flow

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvRoot = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

Write-Host "AnnoWeave setup" -ForegroundColor White
Write-Host "  repository : $ProjectRoot"
Write-Host "  runtime    : $Runtime"

Write-Step "Looking for a compatible Python"
$interpreter = Find-CompatiblePython -Requested $Python -MinimumMinor $MinimumMinor
if ($interpreter.Bits -and "$($interpreter.Bits)".Trim() -ne "64") {
    Write-Fail "The selected Python ($($interpreter.Text)) is $($interpreter.Bits)-bit. AnnoWeave requires 64-bit Python: ONNX Runtime and Qt ship no 32-bit wheels."
    Write-Host "  Install 64-bit Python 3.12: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
Write-Ok "Using $($interpreter.Path) ($($interpreter.Text), $($interpreter.Bits)-bit)"

if ($Recreate -and (Test-Path -LiteralPath $VenvRoot)) {
    Write-Step "Removing the existing virtual environment (-Recreate)"
    Remove-Item -LiteralPath $VenvRoot -Recurse -Force
    Write-Ok "Removed $VenvRoot"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Step "Creating the virtual environment at .venv"
    if ($interpreter.Spec) {
        & py $interpreter.Spec -m venv $VenvRoot
    } else {
        & $interpreter.Path -m venv $VenvRoot
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Fail "Virtual environment creation failed."
        Write-Host "  Retry with a specific interpreter: .\scripts\setup.ps1 -Python `"C:\Path\To\python.exe`"" -ForegroundColor Yellow
        exit 1
    }
    Write-Ok "Created $VenvRoot"
} else {
    Write-Ok "Reusing the existing virtual environment (.venv)"
}

if (-not $SkipInstall) {
    Write-Step "Installing dependencies (downloads PySide6 and ONNX Runtime, a few hundred MB)"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Write-Warn "Upgrading pip failed; continuing with the bundled pip." }
    & $VenvPython -m pip install -e "$ProjectRoot[$Runtime,dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Dependency installation failed. Common causes and fixes:"
        Write-Host "    - No network / proxy required:" -ForegroundColor Yellow
        Write-Host "      .\.venv\Scripts\python.exe -m pip install -e `".[$Runtime]`" --proxy http://host:port"
        Write-Host "    - Corporate TLS interception:" -ForegroundColor Yellow
        Write-Host "      .\.venv\Scripts\python.exe -m pip install -e `".[$Runtime]`" --trusted-host pypi.org --trusted-host files.pythonhosted.org"
        Write-Host "    - Python is 32-bit, or is 3.14+ (unsupported)." -ForegroundColor Yellow
        exit 1
    }
    Write-Ok "Installed annoweave with the '$Runtime' runtime"
} else {
    Write-Warn "Skipped dependency installation (-SkipInstall)"
}

Write-Step "Verifying imports"
& $VenvPython -c "import annoweave; from annoweave.inference.backends import onnxruntime_available; print('annoweave', annoweave.__version__); print('onnxruntime available:', onnxruntime_available())"
if ($LASTEXITCODE -ne 0) {
    Write-Fail "AnnoWeave could not be imported. Re-run with -Recreate to rebuild the environment."
    exit 1
}
Write-Ok "Application imports cleanly"

if (-not $SkipTests) {
    Write-Step "Running the test suite"
    $previousPlatform = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    try {
        & $VenvPython -m pytest
    } finally {
        if ($null -eq $previousPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        else { $env:QT_QPA_PLATFORM = $previousPlatform }
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Tests failed. Please include the output above in an issue."
        exit 1
    }
    Write-Ok "Tests passed"
}

Write-Host ""
if ($script:ProblemCount -gt 0) {
    Write-Host "Setup finished with $script:ProblemCount problem(s)." -ForegroundColor Red
    exit 1
}
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Start AnnoWeave with either command:" -ForegroundColor White
Write-Host "  .\scripts\run.ps1"
Write-Host "  .\.venv\Scripts\Activate.ps1 ; annoweave"
Write-Host ""
Write-Host "First launch is intentionally empty: add a local ONNX model on the Model Library page."
Write-Host "Guide: docs/en/getting-started.md (English) / docs/zh-CN/getting-started.md (中文)"
