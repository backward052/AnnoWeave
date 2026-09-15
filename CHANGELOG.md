# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the project is alpha, minor versions may contain breaking changes.

## [Unreleased]

## [0.3.0] - 2026-09-15

First public source release.

### Added

- Review workspace for single images, selected files, folders, and sampled video
  frames, with box editing on full frames and crops.
- Composable node workflow: full-frame inference, crop cascade, spatial
  association, ROI, counting, rule, and output nodes.
- Model library with ONNX Runtime CPU/GPU backends, detection, classification,
  pose, and OBB task types, plus a per-model smoke test.
- Workflow templates (multi-model full frame, detect → crop → downstream,
  two-model association → crop → downstream) and preflight validation.
- Review sessions, precompute cache, batch jobs with immutable workflow/model
  snapshots, and review or training dataset export.
- Local plugin registry with a manifest protocol and a minimal example plugin.
- Chinese and English interface.
- `scripts/setup.ps1`, `scripts/run.ps1`, and `scripts/build.ps1` for one-command
  setup, launch, and PyInstaller packaging.
- Generic example workflow and plugin under `examples/`.

### Fixed

- `ANNOWEAVE_CONFIG_DIR` now relocates all local state. Previously only the
  inference config and workflow catalog honoured it, while the project databases,
  plugin directory, label catalog, and caches stayed in `%LOCALAPPDATA%`.
- `ReviewPage._differs_from_prediction` called `self` from a static method, so
  reviewing a frame with manual edits raised `NameError`.
- Removed a duplicate `"模型"` key in the translation table that silently
  overrode the intended English label.
- `scripts/check-release.ps1` and the test suite no longer report the documented
  `.venv` directory as a private artifact.

### Changed

- `check-release.ps1` inspects the Git index instead of the working tree, and
  shares its policy with `tests/test_release_policy.py`.
- CI runs lint and tests on Python 3.10, 3.12, and 3.13, plus a packaging job that
  verifies the wheel metadata and bundled assets.
- Documented Python support is 3.10 – 3.13 with 3.12/3.13 recommended.

### Security

- The public repository boundary is enforced by both a release script and a test.

[Unreleased]: https://github.com/backward052/AnnoWeave/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/backward052/AnnoWeave/releases/tag/v0.3.0
