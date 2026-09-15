[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release_policy.ps1")

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Test-ReleaseTokenPresent {
    # Wraps Select-String so an empty candidate list is a non-event instead of an
    # error. `Select-String -Path @()` throws, which would turn a repository that
    # happens to contain no scannable text files into a confusing crash.
    #
    # -Path is mandatory here: Windows PowerShell 5.1 silently matches nothing when
    # FileInfo objects arrive through the pipeline, which would disable the check.
    [CmdletBinding()]
    param(
        [string[]]$Path,
        [Parameter(Mandatory = $true)][string]$Pattern
    )

    if (-not $Path -or $Path.Count -eq 0) { return @() }
    return @(Select-String -Path $Path -SimpleMatch -Pattern $Pattern -ErrorAction SilentlyContinue)
}

function Get-CandidateFiles {
    # Prefer the Git index: it is exactly what a push would publish, and it ignores
    # local-only state such as .venv, build and dist. Run `git add` first.
    Push-Location $ProjectRoot
    try {
        $tracked = @(git ls-files 2>$null)
    } catch {
        $tracked = @()
    } finally {
        Pop-Location
    }
    if ($tracked.Count -gt 0) {
        return @($tracked | ForEach-Object { Join-Path $ProjectRoot ($_ -replace '/', '\') })
    }
    Write-Host "Note: no Git-tracked files found; scanning the working tree instead."
    return @(
        Get-ChildItem -LiteralPath $ProjectRoot -Recurse -File -Force |
            Where-Object { -not (Test-ReleaseExcludedPath -RelativePath $_.FullName.Substring($ProjectRoot.Length).TrimStart('\')) } |
            ForEach-Object { $_.FullName }
    )
}

$candidateFiles = @(Get-CandidateFiles)
$problemCount = 0

if ($candidateFiles.Count -eq 0) {
    Write-Host "Release check FAILED: nothing to check." -ForegroundColor Red
    Write-Host "No Git-tracked files were found and the working-tree fallback matched nothing." -ForegroundColor Yellow
    Write-Host "Run 'git add .' from the repository root, then try again." -ForegroundColor Yellow
    exit 1
}

foreach ($file in $candidateFiles) {
    $relative = $file.Substring($ProjectRoot.Length).TrimStart('\')
    $segments = $relative -split '[\\/]'
    $name = $segments[-1]

    # A forbidden top-level directory must not be tracked at all.
    if ($segments.Count -gt 1 -and $ReleaseForbiddenDirectoryNames -contains $segments[0].ToLowerInvariant()) {
        Write-Host "Forbidden release directory: $relative"
        $problemCount++
    }

    $extension = [System.IO.Path]::GetExtension($name).ToLowerInvariant()
    if ($ReleaseForbiddenExtensions -contains $extension) {
        Write-Host "Forbidden release file type: $relative"
        $problemCount++
    }
}

$textFiles = @($candidateFiles | Where-Object {
    $ReleaseScannedExtensions -contains [System.IO.Path]::GetExtension($_).ToLowerInvariant()
})
foreach ($parts in $ReleaseForbiddenTokenParts) {
    $token = ($parts -join '')
    $hits = Test-ReleaseTokenPresent -Path $textFiles -Pattern $token
    if ($hits.Count -gt 0) {
        $hits | ForEach-Object {
            Write-Host "Forbidden token in $($_.Path.Substring($ProjectRoot.Length).TrimStart('\')):$($_.LineNumber)"
            $problemCount++
        }
    }
}

if ($problemCount -gt 0) {
    Write-Host ""
    Write-Host "Release check FAILED: $problemCount problem(s). See docs/en/release-checklist.md." -ForegroundColor Red
    exit 1
}

# Warnings only: a private staging push may legitimately still contain placeholders.
$placeholderScan = @($textFiles | Where-Object { $ReleasePlaceholderExemptFiles -notcontains (Split-Path $_ -Leaf) })
$placeholderHits = @()
foreach ($placeholder in $ReleasePlaceholderTokens) {
    $placeholderHits += Test-ReleaseTokenPresent -Path $placeholderScan -Pattern $placeholder
}
if ($placeholderHits.Count -gt 0) {
    Write-Host ""
    Write-Host "WARNING: $($placeholderHits.Count) placeholder reference(s) still need your repository URL:" -ForegroundColor Yellow
    $placeholderHits |
        ForEach-Object { $_.Path.Substring($ProjectRoot.Length).TrimStart('\') } |
        Sort-Object -Unique |
        ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Write-Host "Replace 'OWNER' with your GitHub user or organization before making the repository public." -ForegroundColor Yellow
    Write-Host "See docs/zh-CN/publishing-github.md for the step-by-step list." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Release check passed: $($candidateFiles.Count) file(s) checked, no private artifacts or known identifiers." -ForegroundColor Green
