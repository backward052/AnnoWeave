# Security policy

## Reporting a vulnerability

Do not report security vulnerabilities in public issues. Use GitHub's private
[Report a vulnerability](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
flow for this repository, or contact the maintainer privately.

Include the affected version, reproduction steps, impact, and any suggested mitigation.
Please allow time for a fix before publishing details. Public reports must never include
customer media, production workflows, credentials, or local filesystem paths.

## Supported versions

The project is alpha software. Only the latest released minor version receives security fixes.

## Threat model

AnnoWeave runs entirely locally and opens files from untrusted sources. Treat the
following as untrusted input:

- **ONNX model files.** ONNX Runtime parses them; a malicious file may exploit a parser
  or runtime bug. Only load weights from sources you trust.
- **Local plugins.** A plugin is arbitrary Python executed in-process with your privileges.
  Installing a plugin is equivalent to running its author's code. Only install plugins you
  have reviewed.
- **Workflow JSON and review-session files.** These are deserialized into configuration
  objects. The precompute cache uses `pickle` and must only be read from your own
  `ANNOWEAVE_CONFIG_DIR`; do not import cache files from untrusted parties.
- **Media.** Images and video are decoded by OpenCV and Qt/FFmpeg, both of which have
  historically had memory-safety issues. Keep them patched by upgrading the `cpu`/`gpu`
  extras and `opencv-contrib-python-headless`.

## Design properties worth knowing

- No network access is performed by the application itself, and no telemetry is sent.
- Model weights, media, project databases, and configuration stay on the local machine.
- The app never writes inside its own installation directory; all state goes to
  `%LOCALAPPDATA%\AnnoWeave` or `ANNOWEAVE_CONFIG_DIR`.
