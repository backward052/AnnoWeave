# Contributing

Thanks for helping improve AnnoWeave. This project targets Windows 10/11 x64 with 64-bit Python 3.10 – 3.13 (3.12/3.13 recommended).

## Set up

```powershell
git clone https://github.com/backward052/AnnoWeave.git
cd AnnoWeave
.\scripts\setup.ps1          # creates .venv, installs .[cpu,dev], runs the tests
```

## Before opening a pull request

```powershell
.\scripts\check-release.ps1
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

All three must pass; CI runs the same commands on Python 3.10, 3.12, and 3.13.

Keep pull requests focused, and describe the user-visible behavior, how you validated it, and any compatibility limit. Add or update a test whenever you change behavior.

Set `QT_QPA_PLATFORM=offscreen` to run the UI tests without a visible window.

## Code style

- Follow the surrounding style; `ruff check` (rules `E`, `F`, `I`) is the gate.
- Chinese remains the canonical UI source text in `src/annoweave/i18n.py`; add the English
  entry for every new user-visible string and keep the keys unique.
- Resolve local data paths through `src/annoweave/paths.py` so `ANNOWEAVE_CONFIG_DIR` keeps working.
- When you rename a node type or a node parameter, update `examples/workflows/associate-crop-infer.json`.
  `tests/test_examples.py` fails if the example drifts from the node registry.

## Confidentiality rules

Never submit model weights, customer media, production workflows, local databases, absolute
workstation paths, logs, credentials, or code that embeds private class/threshold combinations.
Use invented, neutral data in tests and documentation.

The forbidden-name and forbidden-extension policy lives in `scripts/release_policy.ps1`. Both
`scripts/check-release.ps1` and `tests/test_release_policy.py` read it, so add new rules there
rather than duplicating lists.
