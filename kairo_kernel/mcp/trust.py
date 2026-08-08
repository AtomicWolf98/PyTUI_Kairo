"""Digest-bound trust for MCP server launch and connection configuration."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from kairo_kernel.mcp.models import McpServerConfig, McpTrustError


class McpServerTrustStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def is_trusted(self, config: McpServerConfig) -> bool:
        return self._load().get(config.name) == config.digest

    def trust(self, config: McpServerConfig, expected_digest: str) -> str:
        if not expected_digest or expected_digest != config.digest:
            raise McpTrustError("MCP server configuration changed after review.")
        entries = self._load()
        entries[config.name] = config.digest
        self._save(entries)
        return config.digest

    def revoke(self, server_name: str) -> bool:
        entries = self._load()
        removed = entries.pop(server_name, None) is not None
        if removed:
            self._save(entries)
        return removed

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise McpTrustError(f"Cannot load MCP trust store: {error}") from error
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("entries"), dict):
            raise McpTrustError("MCP trust store is invalid.")
        raw = value["entries"]
        assert isinstance(raw, dict)
        if not all(isinstance(key, str) and isinstance(item, str) for key, item in raw.items()):
            raise McpTrustError("MCP trust entries are invalid.")
        return {str(key): str(item) for key, item in raw.items()}

    def _save(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump({"version": 1, "entries": entries}, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
