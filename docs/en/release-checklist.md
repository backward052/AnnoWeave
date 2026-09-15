# Release checklist

## Allowed in the repository

- `src/annoweave` source and brand assets
- `docs`, `examples`, `tests`
- `pyproject.toml`, both READMEs, `CHANGELOG.md`, `LICENSE`, CI, scripts, and the PyInstaller spec

## Never commit

- Production or test model weights and their hash manifests
- Real images, videos, crops, annotation sets, or export output
- Existing workflow JSON, run snapshots, user sessions, or project databases
- Absolute workstation paths, usernames, network addresses, tokens, certificates, or `.env`
- `.venv`, `build`, `dist`, caches, logs, and design-review scratch files
- Model names, class combinations, thresholds, routing, or association code that reveals a production pipeline

## Before every push

```powershell
.\scripts\check-release.ps1
python -m pytest
python -m ruff check src tests tools
git status --short
git ls-files
```

`check-release.ps1` reads the policy in `scripts/release_policy.ps1` and inspects the
**Git index**, so local-only state such as `.venv`, `build`, and `dist` is ignored
while everything a push would publish is checked. Run `git add` first so new files
are included. The same policy backs `tests/test_release_policy.py`, so the gate and
the test suite cannot disagree.

Then read `git diff --cached` by hand and confirm there are no large binaries or
local paths.

## Releasing a version

1. Move the `Unreleased` entries in `CHANGELOG.md` under the new version and date.
2. Bump `version` in `pyproject.toml` and `__version__` in `src/annoweave/__init__.py` together.
3. Rebuild the executable with a clean environment:

   ```powershell
   .\scripts\setup.ps1 -Recreate
   .\scripts\build.ps1 -Runtime cpu
   ```

4. Verify the wheel and sdist metadata:

   ```powershell
   python -m build
   .\scripts\check-package.ps1
   ```

5. Accept the build on a clean Windows machine with no Python, following
   [building-windows.md](building-windows.md).
6. Tag the commit (`git tag v0.3.0`) and push the tag.
7. Publish a GitHub Release containing the zipped `dist\AnnoWeave` directory and
   its checksum:

   ```powershell
   Get-FileHash .\AnnoWeave-0.3.0-cpu.zip -Algorithm SHA256
   ```

   Do not upload the build environment or any test data.

## Repository settings

- Enable Secret Scanning, Push Protection, and Dependabot alerts.
- Protect `main`: require a pull request and a passing CI run before merging.
- Keep model weights, media, and workflow JSON out of the repository and out of Git LFS.
