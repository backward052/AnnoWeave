"""Public-repository boundary checks.

The forbidden-name and forbidden-extension policy lives in
``scripts/release_policy.ps1`` so that this test and ``scripts/check-release.ps1``
can never drift apart. The test reads that single source of truth instead of
duplicating the lists.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = REPO_ROOT / "scripts" / "release_policy.ps1"


def _policy_array(name: str) -> list[str]:
    """Read a simple ``$Name = @("a", "b")`` array out of the PowerShell policy."""
    text = POLICY_FILE.read_text(encoding="utf-8")
    match = re.search(
        rf"\${name}\s*=\s*@\((.*?)\)\s*\n",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"{name} not found in {POLICY_FILE.name}")
    return [item.strip().strip('"').strip("'") for item in match.group(1).split(",") if item.strip()]


def _tracked_files() -> list[str]:
    """Return the paths Git would publish, or walk the tree when Git is absent."""
    try:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(REPO_ROOT).parts
        and not _is_excluded(path.relative_to(REPO_ROOT).as_posix())
    )


def _is_excluded(relative: str) -> bool:
    excluded = _policy_array("ReleaseScanExcludedDirectories")
    for segment in relative.split("/"):
        lowered = segment.lower()
        for candidate in excluded:
            candidate = candidate.lower()
            if "*" in candidate:
                if re.fullmatch(candidate.replace(".", r"\.").replace("*", ".*"), lowered):
                    return True
            elif lowered == candidate:
                return True
    return False


class ReleasePolicyTests(unittest.TestCase):
    def test_no_forbidden_tracked_directories(self):
        forbidden = {name.lower() for name in _policy_array("ReleaseForbiddenDirectoryNames")}
        offenders = []
        for relative in _tracked_files():
            top = relative.split("/")[0].lower()
            if top in forbidden:
                offenders.append(relative)
        self.assertEqual([], offenders, f"forbidden release directories are tracked: {offenders}")

    def test_no_forbidden_tracked_file_types(self):
        forbidden = {ext.lower() for ext in _policy_array("ReleaseForbiddenExtensions")}
        offenders = [
            relative
            for relative in _tracked_files()
            if Path(relative).suffix.lower() in forbidden
        ]
        self.assertEqual([], offenders, f"forbidden release file types are tracked: {offenders}")

    def test_no_known_private_identifiers(self):
        parts_groups = re.findall(
            r'@\(([^)]*)\)', POLICY_FILE.read_text(encoding="utf-8")
        )
        tokens = [
            "".join(re.findall(r'"([^"]*)"', group))
            for group in parts_groups
            if group.count('"') >= 2 and re.fullmatch(r'\s*"[^"]*"\s*,\s*"[^"]*"\s*', group)
        ]
        self.assertTrue(tokens, "no forbidden-token pairs were parsed from the release policy")
        scanned = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".ps1", ".spec", ".in", ".cfg", ".txt")
        hits = []
        for relative in _tracked_files():
            if Path(relative).suffix.lower() not in scanned:
                continue
            try:
                text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for token in tokens:
                if token in text:
                    hits.append(f"{relative}: {token}")
        self.assertEqual([], hits, f"known private identifiers found: {hits}")

    def test_repository_boundary_helper_ignores_local_tool_state(self):
        # The documented setup flow creates .venv before the release gate runs, so
        # local tool state must never be reported as a private artifact.
        for local_only in (".venv/pyvenv.cfg", "build/lib/x.py", "dist/x/AnnoWeave.exe", ".pytest_cache/CACHEDIR.TAG"):
            self.assertTrue(_is_excluded(local_only), f"{local_only} should be excluded from scanning")
        for tracked in ("src/annoweave/app.py", "examples/workflows/README.md", "scripts/build.ps1"):
            self.assertFalse(_is_excluded(tracked), f"{tracked} should not be excluded from scanning")


if __name__ == "__main__":
    unittest.main()
