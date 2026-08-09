"""Versioned global KernelConfig document: typed load and atomic save.

The store is path-based and platform-neutral; frontends resolve the concrete
location (for example %APPDATA%/Kairo/config-v1.json on Windows).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import PROTOCOL_VERSION, McpServerConfig
from kairo_kernel.services.providers import ProviderCatalogSnapshot, ProviderRoleMapping

CONFIG_DOCUMENT_VERSION = 1

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class KernelConfigDocument:
    """Global user-level configuration; secret values never appear here."""

    version: int = CONFIG_DOCUMENT_VERSION
    profiles: tuple[ProviderProfile, ...] = ()
    roles: tuple[ProviderRoleMapping, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    default_profile_id: ProfileId | None = None
    theme: str = "default"
    keybindings: tuple[tuple[str, str], ...] = ()
    recent_workspaces: tuple[str, ...] = ()
    revision: int = 0


DocumentTransform = Callable[[KernelConfigDocument], KernelConfigDocument]


class KernelConfigStore:
    """Load and atomically save one versioned JSON document at a fixed path."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> KernelResult[KernelConfigDocument]:
        try:
            payload = await asyncio.to_thread(self._read_sync)
        except FileNotFoundError:
            return _failure(ErrorCode.NOT_FOUND, "Configuration document was not found.", "config.document.load")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return _failure(
                ErrorCode.CONFIG_INVALID,
                f"Configuration document is not valid UTF-8 JSON: {exc}",
                "config.document.load",
            )
        except OSError:
            return _failure(
                ErrorCode.CONFIG_PERSISTENCE_FAILED,
                "Configuration document could not be read.",
                "config.document.load",
                retryable=True,
            )
        try:
            return KernelResult.success(document_from_json(payload))
        except ValueError as exc:
            return _failure(ErrorCode.CONFIG_INVALID, str(exc), "config.document.load")

    async def save(self, document: KernelConfigDocument) -> KernelResult[KernelConfigDocument]:
        if document.version != CONFIG_DOCUMENT_VERSION:
            return _failure(
                ErrorCode.CONFIG_INVALID,
                f"Unsupported configuration document version: {document.version}.",
                "config.document.save",
            )
        try:
            await asyncio.to_thread(self._write_sync, document)
        except OSError:
            return _failure(
                ErrorCode.CONFIG_PERSISTENCE_FAILED,
                "Configuration document could not be written.",
                "config.document.save",
                retryable=True,
            )
        return KernelResult.success(document)

    async def update(
        self,
        expected_revision: int,
        transform: DocumentTransform,
    ) -> KernelResult[int]:
        """Optimistically mutate the document under a single-process lock.

        The transform receives the current document and returns the updated
        document; the store persists it with ``revision = expected_revision + 1``.
        A stale ``expected_revision`` (or a concurrent writer that already
        advanced the document) fails with ``ErrorCode.CONFLICT``.
        """
        async with self._lock:
            loaded = await self.load()
            if loaded.error is not None and loaded.error.code is not ErrorCode.NOT_FOUND:
                return KernelResult.failure(loaded.error)
            document = loaded.value if loaded.value is not None else KernelConfigDocument()
            if document.revision != expected_revision:
                return _failure(
                    ErrorCode.CONFLICT,
                    "Configuration revision has changed.",
                    "config.document.update",
                )
            try:
                updated = transform(document)
            except ValueError as exc:
                return _failure(
                    ErrorCode.CONFIG_INVALID,
                    f"Configuration transform failed: {exc}",
                    "config.document.update",
                )
            if not isinstance(updated, KernelConfigDocument):
                return _failure(
                    ErrorCode.CONFIG_INVALID,
                    "Configuration transform must return a KernelConfigDocument.",
                    "config.document.update",
                )
            committed = replace(updated, revision=expected_revision + 1)
            saved = await self.save(committed)
            if saved.error is not None:
                return KernelResult.failure(saved.error)
            return KernelResult.success(committed.revision)

    def _read_sync(self) -> object:
        with self._path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_sync(self, document: KernelConfigDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(document_to_json(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except Exception:
            with suppress(OSError):
                os.unlink(temporary)
            raise


class DocumentProviderCatalog:
    """Persist the provider catalog inside the global configuration document."""

    def __init__(self, store: KernelConfigStore) -> None:
        self._store = store

    async def load(self) -> KernelResult[ProviderCatalogSnapshot]:
        loaded = await self._store.load()
        if loaded.error is not None:
            if loaded.error.code is ErrorCode.NOT_FOUND:
                return KernelResult.success(ProviderCatalogSnapshot(0))
            return KernelResult.failure(loaded.error)
        assert loaded.value is not None
        return KernelResult.success(ProviderCatalogSnapshot(0, loaded.value.profiles, loaded.value.roles))

    async def save(self, snapshot: ProviderCatalogSnapshot) -> KernelResult[ProviderCatalogSnapshot]:
        loaded = await self._store.load()
        if loaded.error is not None and loaded.error.code is not ErrorCode.NOT_FOUND:
            return KernelResult.failure(loaded.error)
        expected = loaded.value.revision if loaded.value is not None else 0
        updated = await self._store.update(
            expected,
            lambda document: replace(document, profiles=snapshot.profiles, roles=snapshot.roles),
        )
        if updated.error is not None:
            return KernelResult.failure(updated.error)
        return KernelResult.success(snapshot)


def document_to_json(document: KernelConfigDocument) -> dict[str, object]:
    return {
        "version": document.version,
        "revision": document.revision,
        "default_profile_id": str(document.default_profile_id) if document.default_profile_id is not None else None,
        "theme": document.theme,
        "profiles": [thaw_json(profile.to_json_value()) for profile in document.profiles],
        "roles": [{"role": mapping.role, "profile_id": str(mapping.profile_id)} for mapping in document.roles],
        "mcp_servers": [_server_to_json(server) for server in document.mcp_servers],
        "keybindings": [[key, command] for key, command in document.keybindings],
        "recent_workspaces": list(document.recent_workspaces),
    }


def document_from_json(payload: object) -> KernelConfigDocument:
    if not isinstance(payload, dict):
        raise ValueError("Configuration document must be a JSON object.")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("Configuration document requires an integer 'version'.")
    if version != CONFIG_DOCUMENT_VERSION:
        raise ValueError(f"Unsupported configuration document version: {version}.")
    revision = _int(payload, "revision", 0)
    if revision < 0:
        raise ValueError("Configuration document revision cannot be negative.")
    profiles: list[ProviderProfile] = []
    for item in _list(payload, "profiles"):
        frozen = freeze_json(item)
        if not isinstance(frozen, JsonObject):
            raise ValueError("Provider profile entries must be objects.")
        try:
            profiles.append(ProviderProfile.from_json_value(frozen))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid provider profile entry: {exc}") from exc
    roles: list[ProviderRoleMapping] = []
    for item in _list(payload, "roles"):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or not isinstance(item.get("profile_id"), str)
        ):
            raise ValueError("Provider role entries must contain role and profile_id strings.")
        roles.append(ProviderRoleMapping(item["role"], ProfileId(item["profile_id"])))
    keybindings: list[tuple[str, str]] = []
    for item in _list(payload, "keybindings"):
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item):
            raise ValueError("Keybinding entries must be [key, command] string pairs.")
        keybindings.append((item[0], item[1]))
    default_profile = _optional_str(payload, "default_profile_id")
    return KernelConfigDocument(
        version,
        tuple(profiles),
        tuple(roles),
        tuple(_server_from_json(item) for item in _list(payload, "mcp_servers")),
        ProfileId(default_profile) if default_profile is not None else None,
        _str(payload, "theme", "default"),
        tuple(keybindings),
        tuple(_str_list(payload, "recent_workspaces")),
        revision,
    )


def _server_to_json(server: McpServerConfig) -> dict[str, object]:
    return {
        "name": server.name,
        "transport": server.transport,
        "command": server.command,
        "arguments": list(server.arguments),
        "url": server.url,
        "environment": [[key, value] for key, value in server.environment],
        "environment_allowlist": list(server.environment_allowlist),
        "headers": [[key, value] for key, value in server.headers],
        "protocol_version": server.protocol_version,
    }


def _server_from_json(item: object) -> McpServerConfig:
    if not isinstance(item, dict):
        raise ValueError("MCP server entries must be objects.")
    name = item.get("name")
    transport = item.get("transport")
    if not isinstance(name, str) or not name.strip() or not isinstance(transport, str) or not transport.strip():
        raise ValueError("MCP server entries require name and transport strings.")
    return McpServerConfig(
        name,
        transport,
        command=_str(item, "command"),
        arguments=tuple(_str_list(item, "arguments")),
        url=_str(item, "url"),
        environment=_str_pairs(item, "environment"),
        environment_allowlist=tuple(_str_list(item, "environment_allowlist")),
        headers=_str_pairs(item, "headers"),
        protocol_version=_str(item, "protocol_version") or PROTOCOL_VERSION,
    )


def _str(payload: dict[str, object], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"Configuration field '{key}' must be a string.")
    return value


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Configuration field '{key}' must be a string.")
    return value


def _int(payload: dict[str, object], key: str, default: int = 0) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Configuration field '{key}' must be an integer.")
    return value


def _list(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"Configuration field '{key}' must be a list.")
    return value


def _str_list(payload: dict[str, object], key: str) -> list[str]:
    strings: list[str] = []
    for item in _list(payload, key):
        if not isinstance(item, str):
            raise ValueError(f"Configuration field '{key}' must be a list of strings.")
        strings.append(item)
    return strings


def _str_pairs(payload: dict[str, object], key: str) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in _list(payload, key):
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item):
            raise ValueError(f"Configuration field '{key}' must be a list of string pairs.")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def _failure(
    code: ErrorCode,
    message: str,
    operation: str,
    *,
    retryable: bool = False,
) -> KernelResult[ResultT]:
    return KernelResult.failure(KernelError(code, message, retryable, operation))
