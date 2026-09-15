"""Runtime data locations.

Two environment variables matter:

``ANNOWEAVE_CONFIG_DIR``
    Optional. When set, **all** local AnnoWeave state is written under this
    directory instead of ``%LOCALAPPDATA%\\AnnoWeave``. This covers the model
    library, the workflow catalog, per-project review databases, the label
    catalog, the plugin directory, review sessions, and the precompute cache.

``LOCALAPPDATA``
    Windows default root. On non-Windows platforms ``~/.annoweave`` is used.

Before this module existed the override only affected ``inference_config.json``
and the workflow catalog, while the project databases, plugin directory, and
label catalog silently stayed in ``%LOCALAPPDATA%``. Every call site now resolves
its path here so the documented behaviour is the real behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Environment variable that relocates all local application data.
CONFIG_DIR_ENV = "ANNOWEAVE_CONFIG_DIR"

#: Directory name used under the platform application-data root.
APP_DIR_NAME = "AnnoWeave"


def config_root() -> Path | None:
    """Return the explicit override root, or ``None`` when unset/blank."""
    value = os.environ.get(CONFIG_DIR_ENV, "").strip()
    return Path(value).expanduser() if value else None


def app_root() -> Path:
    """Return the directory that holds all local AnnoWeave state.

    When ``ANNOWEAVE_CONFIG_DIR`` is set this is that directory itself, so a user
    who sets it gets a single self-contained data folder.
    """
    override = config_root()
    if override is not None:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".annoweave"


def data_path(*parts: str) -> Path:
    """Join ``parts`` onto :func:`app_root`."""
    return app_root().joinpath(*parts)
