# Verifies the built wheel, sdist, and frozen application before publishing.
#
#   python -m build
#   .\scripts\check-package.ps1
#
# Reads the archives directly so this check never imports AnnoWeave and therefore
# does not need PySide6 or ONNX Runtime installed.

[CmdletBinding()]
param([string]$DistPath = "dist")

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dist = if ([System.IO.Path]::IsPathRooted($DistPath)) { $DistPath } else { Join-Path $ProjectRoot $DistPath }

# Print one readable line instead of a PowerShell stack trace.
function Stop-Check {
    param([string]$Message)
    Write-Host ""
    Write-Host "Package check FAILED: $Message" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $dist)) {
    Stop-Check "no '$DistPath' directory. Run 'python -m build' first."
}

$wheels = @(Get-ChildItem -LiteralPath $dist -Filter *.whl)
$sdists = @(Get-ChildItem -LiteralPath $dist -Filter *.tar.gz)
if ($wheels.Count -eq 0) {
    Stop-Check "no wheel found in $dist. Run 'python -m build' first."
}

$expectedAssets = @(
    "annoweave/assets/annoweave.ico",
    "annoweave/assets/annoweave-logo.png",
    "annoweave/assets/annoweave-mark.svg",
    "annoweave/assets/getting-started.zh-CN.md"
)

# Read-only archive inspection; the wheel is a zip file.
Add-Type -AssemblyName System.IO.Compression.FileSystem

foreach ($wheel in $wheels) {
    Write-Host "Checking $($wheel.Name)"
    $archive = [System.IO.Compression.ZipFile]::OpenRead($wheel.FullName)
    try {
        $names = $archive.Entries | ForEach-Object { $_.FullName }

        foreach ($asset in $expectedAssets) {
            if (-not ($names | Where-Object { $_.EndsWith($asset) })) {
                Stop-Check "$($wheel.Name) is missing the packaged asset '$asset'. Check [tool.setuptools.package-data] in pyproject.toml."
            }
        }

        if (-not ($names | Where-Object { $_ -match '(^|/)LICENSE$' })) {
            Stop-Check "$($wheel.Name) is missing LICENSE. Check the 'license-files' field in pyproject.toml."
        }

        $metadataEntry = $archive.Entries | Where-Object { $_.FullName -match '\.dist-info/METADATA$' } | Select-Object -First 1
        if (-not $metadataEntry) { Stop-Check "$($wheel.Name) has no METADATA file." }
        $reader = New-Object System.IO.StreamReader($metadataEntry.Open())
        try { $metadata = $reader.ReadToEnd() } finally { $reader.Dispose() }

        if ($metadata -notmatch '(?m)^License-Expression:\s*MIT\s*$') {
            Stop-Check "$($wheel.Name) METADATA does not declare 'License-Expression: MIT'. Keep the license expression and drop any 'License ::' classifier (PEP 639)."
        }
        if ($metadata -match '(?m)^Classifier:\s*License ::') {
            Stop-Check "$($wheel.Name) still declares a legacy 'License ::' classifier, which setuptools rejects together with a license expression."
        }

        # setuptools canonicalises the specifier order, so check the parts, not the string.
        $requiresPython = ([regex]::Match($metadata, '(?m)^Requires-Python:\s*(.+?)\s*$')).Groups[1].Value
        if ($requiresPython -notmatch '>=3\.10' -or $requiresPython -notmatch '<3\.14') {
            Stop-Check "$($wheel.Name) has an unexpected Requires-Python ('$requiresPython'). Expected a >=3.10,<3.14 bound."
        }

        $runtimeDeps = [regex]::Matches($metadata, '(?m)^Requires-Dist:\s*([A-Za-z0-9_.\-]+)') |
            ForEach-Object { $_.Groups[1].Value.ToLowerInvariant() }
        foreach ($required in @("pyside6", "numpy", "opencv-contrib-python-headless")) {
            if ($runtimeDeps -notcontains $required) {
                Stop-Check "$($wheel.Name) is missing the runtime dependency '$required'."
            }
        }

        Write-Host "  OK: $($names.Count) files, MIT license expression, assets present, Requires-Python $requiresPython" -ForegroundColor Green
    } finally {
        $archive.Dispose()
    }
}

foreach ($sdist in $sdists) {
    Write-Host "Checking $($sdist.Name)"
    $entries = & tar -tzf $sdist.FullName
    if ($LASTEXITCODE -ne 0) {
        Stop-Check "could not read $($sdist.Name). Expected a .tar.gz source distribution."
    }
    foreach ($required in @("LICENSE", "README.md", "README_zh-CN.md", "pyproject.toml", "CHANGELOG.md")) {
        if (-not ($entries | Where-Object { $_ -match "(^|/)$([regex]::Escape($required))$" })) {
            Stop-Check "$($sdist.Name) is missing '$required'. Check MANIFEST.in."
        }
    }
    if (-not ($entries | Where-Object { $_ -match 'annoweave/assets/annoweave\.ico$' })) {
        Stop-Check "$($sdist.Name) is missing the packaged brand assets. Check MANIFEST.in."
    }
    # The README animation is ~2 MB and exists only for GitHub to render. MANIFEST.in prunes
    # docs/assets; confirm that prune still holds so the sdist does not silently grow.
    if ($entries | Where-Object { $_ -match 'docs/assets/.*\.gif$' }) {
        Stop-Check "$($sdist.Name) bundles the README demo animation. MANIFEST.in should keep pruning docs/assets."
    }
    Write-Host "  OK: $($entries.Count) entries, license and assets present" -ForegroundColor Green
}

# The frozen application is verified when dist\AnnoWeave exists. PyInstaller leaves a
# second, non-runnable AnnoWeave.exe in build\; launching that one fails with
# "Failed to load Python DLL" because no _internal\pythonXXX.dll sits beside it.
$frozenExe = Join-Path $dist "AnnoWeave\AnnoWeave.exe"
if (Test-Path -LiteralPath $frozenExe) {
    Write-Host "Checking the frozen application"

    $internal = Join-Path $dist "AnnoWeave\_internal"
    $pythonDll = @(Get-ChildItem -LiteralPath $internal -Filter "python3*.dll" -ErrorAction SilentlyContinue)
    if ($pythonDll.Count -eq 0) {
        Stop-Check "$DistPath\AnnoWeave\_internal has no python3*.dll, so the executable cannot start. Rerun .\scripts\build.ps1."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $internal "PySide6"))) {
        Stop-Check "$DistPath\AnnoWeave\_internal has no PySide6 directory, so the Qt runtime is missing. Rerun .\scripts\build.ps1."
    }

    $otherExecutables = @(
        Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "build") -Recurse -Filter "AnnoWeave.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $frozenExe }
    )
    if ($otherExecutables.Count -gt 0) {
        Stop-Check "found $($otherExecutables.Count) extra AnnoWeave.exe under build\; those fail with 'Failed to load Python DLL'. Rerun .\scripts\build.ps1, which removes them."
    }

    Write-Host "  OK: python DLL, Qt runtime present, exactly one runnable AnnoWeave.exe" -ForegroundColor Green
} else {
    Write-Host "Skipping frozen application check (run .\scripts\build.ps1 to produce dist\AnnoWeave)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Package check passed." -ForegroundColor Green
