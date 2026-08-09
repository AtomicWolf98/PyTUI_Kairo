"""Versioned global configuration document (TUI-owned mirror of the kernel schema).

Mirrors ``kairo_kernel.services.config_document``'s JSON schema without importing
that private module (AST boundary): profiles serialize through the public
``ProviderProfile`` contract helpers; roles/mcp_servers are plain JSON that
round-trip verbatim. No secret values are ever stored here — only opaque
references (secret_id / env-var names).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.providers import ProviderProfile

DOCUMENT_VERSION = 1


@dataclass(frozen=True)
class RoleMapping:
    role: str
    profile_id: ProfileId


@dataclass(frozen=True)
class ConfigDocument:
    version: int = DOCUMENT_VERSION
    profiles: tuple[ProviderProfile, ...] = ()
    roles: tuple[RoleMapping, ...] = ()
    mcp_servers: tuple[dict[str, object], ...] = ()
    default_profile_id: ProfileId | None = None
    theme: str = "default"
    reduced_motion: bool = False
    keybindings: tuple[tuple[str, str], ...] = ()
    recent_workspaces: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Setup page becomes the default when no provider profile exists."""
        return not self.profiles

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "default_profile_id": str(self.default_profile_id) if self.default_profile_id is not None else None,
            "theme": self.theme,
            "reduced_motion": self.reduced_motion,
            "profiles": [thaw_json(profile.to_json_value()) for profile in self.profiles],
            "roles": [{"role": mapping.role, "profile_id": str(mapping.profile_id)} for mapping in self.roles],
            "mcp_servers": [dict(server) for server in self.mcp_servers],
            "keybindings": [[key, command] for key, command in self.keybindings],
            "recent_workspaces": list(self.recent_workspaces),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ConfigDocument:
        if not isinstance(payload, dict):
            raise ValueError("Configuration document must be a JSON object.")
        version = payload.get("version")
        if version != DOCUMENT_VERSION:
            raise ValueError(f"Unsupported configuration document version: {version!r}.")
        profiles = tuple(
            ProviderProfile.from_json_value(_frozen(item)) for item in _as_list(payload.get("profiles", []))
        )
        roles = tuple(
            RoleMapping(str(item["role"]), ProfileId(str(item["profile_id"])))
            for item in _as_dicts(payload.get("roles", []))
        )
        mcp_servers = tuple(dict(item) for item in _as_dicts(payload.get("mcp_servers", [])))
        default_profile_id_value = payload.get("default_profile_id")
        default_profile_id = (
            ProfileId(str(default_profile_id_value)) if default_profile_id_value else None
        )
        theme = str(payload.get("theme", "default"))
        reduced_motion = bool(payload.get("reduced_motion", False))
        keybindings = tuple(
            (str(pair[0]), str(pair[1])) for pair in _as_pairs(payload.get("keybindings", []))
        )
        recent_workspaces = tuple(str(item) for item in _as_list(payload.get("recent_workspaces", [])))
        return cls(
            version,
            profiles,
            roles,
            mcp_servers,
            default_profile_id,
            theme,
            reduced_motion,
            keybindings,
            recent_workspaces,
        )


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Configuration document array field is invalid.")
    return value


def _as_dicts(value: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            raise ValueError("Configuration document object field is invalid.")
        result.append(item)
    return result


def _as_pairs(value: object) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for item in _as_list(value):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("Configuration document keybinding pair must be a two-entry array.")
        result.append((str(item[0]), str(item[1])))
    return result


def _frozen(value: object) -> JsonObject:
    frozen = freeze_json(value)
    if not isinstance(frozen, JsonObject):
        raise ValueError("Provider profile must be a JSON object.")
    return frozen


class ConfigDocumentAdapter:
    """Load and atomically save one versioned document at a fixed path."""

    def __init__(self, path: Path, *, safe_mode: bool = False) -> None:
        self.path = path
        self.safe_mode = safe_mode
        self.last_error: str | None = None

    def load(self) -> ConfigDocument:
        self.last_error = None
        if not self.path.exists():
            return ConfigDocument()
        try:
            text = self.path.read_text(encoding="utf-8")
            payload = json.loads(text)
            return ConfigDocument.from_dict(payload)
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError) as exc:
            self.last_error = f"Configuration could not be loaded: {exc}"
            return ConfigDocument()

    def save(self, document: ConfigDocument) -> None:
        """Atomically persist the document; no-op in safe mode (no persisted writes)."""
        if self.safe_mode:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=f"{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
