"""Filesystem paths for Kairo's global (user-level) state: the configuration
document and the skill trust store (kept outside any workspace)."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "Kairo"
CONFIG_FILE_NAME = "config-v1.json"


def default_config_dir() -> Path:
    """User config directory for Kairo.

    Windows: %APPDATA%\\Kairo  (platformdirs user_config_dir)
    macOS:   ~/Library/Application Support/Kairo
    Linux:   ~/.config/Kairo
    """
    return Path(user_config_dir(APP_NAME))


def default_trust_dir() -> Path:
    """User data directory for the skill trust store.

    Never inside a workspace: the kernel's fail-closed check rejects a trust
    store under the workspace root, so the store lives in the per-user data
    dir (platformdirs user_data_dir) like the global config document.
    """
    return Path(user_data_dir(APP_NAME)) / "trust"


def resolve_config_path(override: str | Path | None = None) -> Path:
    """Return the config document path; ``override`` (--config) wins."""
    if override is not None:
        return Path(override).expanduser()
    return default_config_dir() / CONFIG_FILE_NAME
