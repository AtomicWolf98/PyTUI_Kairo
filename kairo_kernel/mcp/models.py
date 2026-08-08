"""Internal MCP server configuration and catalog models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

PROTOCOL_VERSION = "2026-07-28"


class McpError(RuntimeError):
    pass


class McpTrustError(McpError):
    pass


class McpProtocolError(McpError):
    pass


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: str
    command: str = ""
    arguments: tuple[str, ...] = ()
    url: str = ""
    environment: tuple[tuple[str, str], ...] = ()
    environment_allowlist: tuple[str, ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    protocol_version: str = PROTOCOL_VERSION

    @property
    def digest(self) -> str:
        value = {
            "arguments": self.arguments,
            "command": self.command,
            "environment": self.environment,
            "environment_allowlist": self.environment_allowlist,
            "headers": self.headers,
            "name": self.name,
            "protocol_version": self.protocol_version,
            "transport": self.transport,
            "url": self.url,
        }
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CatalogEntry:
    namespace: str
    local_name: str
    qualified_name: str
    raw: dict[str, object]


@dataclass(frozen=True)
class McpCatalog:
    server_name: str
    tools: tuple[CatalogEntry, ...] = ()
    resources: tuple[CatalogEntry, ...] = ()
    prompts: tuple[CatalogEntry, ...] = ()

    def all_entries(self) -> tuple[CatalogEntry, ...]:
        return self.tools + self.resources + self.prompts


def qualified_name(server: str, namespace: str, local_name: str) -> str:
    safe_server = "".join(character if character.isalnum() or character == "_" else "_" for character in server)
    safe_local = "".join(character if character.isalnum() or character in "_-" else "_" for character in local_name)
    return f"mcp__{safe_server}__{namespace}__{safe_local}"
