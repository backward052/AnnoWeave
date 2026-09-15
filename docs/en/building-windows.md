# Building for Windows

## Environment

- Windows 10/11 x64
- 64-bit CPython 3.10 – 3.13 (3.12/3.13 recommended)
- PowerShell 5.1 or 7
- CPU builds need no CUDA. GPU builds need an NVIDIA driver and runtime matching the `onnxruntime-gpu` version.

## Standard build

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1                 # creates .venv and installs dependencies
.\scripts\build.ps1 -Runtime cpu
```

`build.ps1` reuses `.venv`, installs the `build` extra, runs the tests, and then
drives `packaging/pyinstaller/annoweave.spec` to produce a one-folder application.
The one-folder layout starts faster than single-file mode and makes it far easier to
inspect the Qt and ONNX Runtime dynamic libraries. It never hard-codes a Python
minor version: if `.venv` is missing it delegates to `setup.ps1`.

GPU build:

```powershell
.\scripts\build.ps1 -Runtime gpu
```

Intermediate options: `-SkipInstall` reuses the current environment,
`-SkipTests` skips the test gate.

## Verify the package metadata

```powershell
python -m build
.\scripts\check-package.ps1
```

This reads the wheel and sdist directly and confirms the license expression, the
`Requires-Python` bound, the runtime dependencies, and the bundled brand assets.

## Acceptance

Copy the whole `dist\AnnoWeave` directory to a clean Windows machine with no Python
installed and verify:

1. `AnnoWeave.exe` starts, and the logo, Chinese, and English UI all render.
2. Opening image and video folders allows switching between media.
3. Adding a test model makes **Test selected model** succeed.
4. The workflow preflight check, running the current frame, box editing, and crop editing all work.
5. A batch job can be cancelled and the completed manifest is still readable.
6. Output paths containing Chinese characters, spaces, and long directory names are writable.

Publish the entire `dist\AnnoWeave` directory, never the `.exe` alone. Do not place
test models, test media, or local configuration inside that directory.

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| Double-clicking the `.exe` reports `Failed to load Python DLL ... build\pyinstaller\...\pythonXXX.dll` | **You opened the wrong file.** The `build\` directory contains a same-named `AnnoWeave.exe` — a PyInstaller intermediate with no `pythonXXX.dll` beside it. Run `dist\AnnoWeave\AnnoWeave.exe`. `build.ps1` now deletes the intermediate copy after a successful build, and `check-package.ps1` verifies exactly one runnable executable remains. |
| The build stops with `PermissionError: [WinError 5] Access is denied` | A previous `AnnoWeave.exe` is still running and holds a lock on the old output. Close the window or run `Stop-Process -Name AnnoWeave`, then rebuild. `build.ps1` now detects this before starting. |
| Missing Qt DLLs | Confirm the whole output directory was copied, then rebuild with `--clean` (the default in `build.ps1`). A stale `build\pyinstaller` cache is the usual cause. |
| ONNX Runtime fails to load | Check that CPU and GPU packages are not mixed in one environment, and that the target machine has the required runtime libraries. |
| Windows Defender warns about an unknown publisher | Sign the executable and installer with your organization's code-signing certificate before a public release. |
| `py -3.11` / `py -3.12` reports *No suitable Python runtime found* | `build.ps1` no longer pins a version. Run `.\scripts\setup.ps1`, which picks a supported interpreter automatically, or pass `-Python` to `setup.ps1`. |
| Path length errors | Keep the repository and `.venv` on a short path such as `D:\AnnoWeave`. Qt is pinned to the 6.8 series to limit path risk. |
| The executable starts but shows an empty workspace | Expected: no weights are bundled. Add a model on the Model Library page. |
| `pip install` fails during the build | Run `.\scripts\setup.ps1 -Recreate` and read its output; it names proxy and TLS workarounds explicitly. |
