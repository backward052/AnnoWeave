# Shared policy for the public-repository boundary.
#
# Both scripts/check-release.ps1 and tests/release_policy.py read this file, so the
# release gate and the test suite can never disagree about what is forbidden.
#
# Note: the *directory* names below are checked with -In. They name top-level
# directories that must never be committed. Local-only working directories such as
# .venv, build, dist and the tool caches are excluded from scanning rather than
# treated as findings: the documented setup flow creates .venv before the release
# check runs, so flagging it would make the documented workflow impossible.

$ReleaseForbiddenDirectoryNames = @(
    "config",
    "workflows",
    "sessions",
    "projects",
    "outputs",
    "exports",
    "datasets",
    "models",
    "weights",
    "logs"
)

# Directory names skipped while walking the working tree. These are local tool
# state, not release content.
$ReleaseScanExcludedDirectories = @(
    ".git",
    ".venv",
    "venv",
    ".env",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    ".vs",
    "node_modules",
    "*.egg-info"
)

$ReleaseForbiddenExtensions = @(
    ".onnx", ".pt", ".pth", ".engine", ".plan", ".trt", ".bin",
    ".db", ".sqlite", ".sqlite3", ".log",
    ".mp4", ".avi", ".mov", ".mkv", ".webm"
)

# Building blocks for tokens that must not leak into the public tree. Kept split so
# that this policy file never contains the identifiers it is meant to catch.
$ReleaseForbiddenTokenParts = @(
    @("tes", "t_data"),
    @("kw", "code"),
    @("head", "person"),
    @("phone", "det"),
    @("police", "cls"),
    @("bms", "behavior_review")
)

$ReleaseScannedExtensions = @(".py", ".md", ".json", ".toml", ".yml", ".yaml", ".ps1", ".spec", ".in", ".cfg", ".txt")

# Placeholders that must be replaced before the repository is made public. These are
# reported as warnings, not failures, because a private staging push may still want them.
$ReleasePlaceholderTokens = @(
    "github.com/OWNER/",
    "your-repository-url"
)

# Files that legitimately contain the placeholder tokens above because they define them.
$ReleasePlaceholderExemptFiles = @("release_policy.ps1", "check-release.ps1")


function Test-ReleaseExcludedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    foreach ($segment in ($RelativePath -split '[\\/]')) {
        if (-not $segment) { continue }
        $lower = $segment.ToLowerInvariant()
        foreach ($excluded in $ReleaseScanExcludedDirectories) {
            if ($lower -eq $excluded.ToLowerInvariant()) { return $true }
            if ($excluded.Contains('*') -and $lower -like $excluded.ToLowerInvariant()) { return $true }
        }
    }
    return $false
}
