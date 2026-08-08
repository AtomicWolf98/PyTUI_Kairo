"""Authorization, canonical-path, command and network policies."""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from kairo_kernel.contracts.enums import AuthorizationMode, OperationScope


class PolicyViolation(ValueError):
    """Raised when an operation violates a mandatory kernel policy."""


class AuthorizationPolicy:
    """Stable manual/auto/yolo authorization matrix."""

    async def is_authorized(self, mode: AuthorizationMode, scope: OperationScope) -> bool:
        if mode is AuthorizationMode.YOLO:
            return True
        if mode is AuthorizationMode.AUTO:
            return scope is OperationScope.INTERNAL
        return False


class WorkspacePathPolicy:
    """Resolve paths canonically and reject workspace/symlink escapes."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise PolicyViolation(f"Workspace is not a directory: {self.root}")

    def resolve(self, value: str, *, must_exist: bool = False) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as exc:
            raise PolicyViolation(f"Invalid path {value!r}: {exc}") from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PolicyViolation(f"Path escapes workspace: {value}") from exc
        return resolved

    def scope(self, value: str) -> OperationScope:
        try:
            self.resolve(value)
        except PolicyViolation:
            return OperationScope.EXTERNAL
        return OperationScope.INTERNAL


_DESTRUCTIVE_COMMAND = re.compile(
    r"(?i)(?:^|\s)(?:rm\s+-[^\r\n]*r|rmdir\s+/s|del\s+/[sq]|format\s+|diskpart\b|git\s+reset\s+--hard\b)"
)
_SYSTEM_COMMAND = re.compile(
    r"(?i)(?:^|\s)(?:sudo\b|runas\b|reg(?:\.exe)?\s+(?:add|delete)\b|sc(?:\.exe)?\s+(?:create|delete|stop)\b|shutdown\b)"
)


class CommandPolicy:
    def __init__(self, workspace: WorkspacePathPolicy, deny_patterns: tuple[str, ...] = ()) -> None:
        self.workspace = workspace
        self._denied = tuple(re.compile(pattern, re.IGNORECASE) for pattern in deny_patterns)

    def classify(self, command: str) -> OperationScope:
        if not command.strip():
            raise PolicyViolation("Command must not be empty.")
        if any(pattern.search(command) for pattern in self._denied):
            raise PolicyViolation("Command matches a deny rule.")
        if _DESTRUCTIVE_COMMAND.search(command):
            return OperationScope.DESTRUCTIVE
        if _SYSTEM_COMMAND.search(command):
            return OperationScope.SYSTEM
        absolute_paths = re.findall(r'(?i)(?:[A-Z]:[\\/][^\s"\']+|/(?:[^\s"\']+))', command)
        if any(self.workspace.scope(path) is OperationScope.EXTERNAL for path in absolute_paths):
            return OperationScope.EXTERNAL
        return OperationScope.INTERNAL


@dataclass(frozen=True)
class NetworkTarget:
    url: str
    host: str
    addresses: tuple[str, ...]


class NetworkPolicy:
    """Validate every request and redirect target against SSRF controls."""

    def __init__(
        self,
        *,
        allow_hosts: tuple[str, ...] = (),
        deny_hosts: tuple[str, ...] = (),
        deny_private: bool = True,
    ) -> None:
        self.allow_hosts = tuple(host.lower().rstrip(".") for host in allow_hosts)
        self.deny_hosts = tuple(host.lower().rstrip(".") for host in deny_hosts)
        self.deny_private = deny_private

    @staticmethod
    def _matches(host: str, rule: str) -> bool:
        return host == rule or host.endswith("." + rule)

    def validate(self, url: str) -> NetworkTarget:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise PolicyViolation("Only absolute HTTP(S) URLs are allowed.")
        if parsed.username or parsed.password:
            raise PolicyViolation("Credentials in URLs are not allowed.")
        host = parsed.hostname.lower().rstrip(".")
        if self.allow_hosts and not any(self._matches(host, rule) for rule in self.allow_hosts):
            raise PolicyViolation(f"Host is not allowlisted: {host}")
        if any(self._matches(host, rule) for rule in self.deny_hosts):
            raise PolicyViolation(f"Host is denied: {host}")
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise PolicyViolation(f"Unable to resolve host {host}: {exc}") from exc
        addresses = tuple(sorted({str(item[4][0]) for item in infos}))
        if not addresses:
            raise PolicyViolation(f"Host has no usable addresses: {host}")
        if self.deny_private:
            for value in addresses:
                address = ipaddress.ip_address(value)
                if not address.is_global:
                    raise PolicyViolation(f"Private or non-global network target is denied: {value}")
        return NetworkTarget(url, host, addresses)
