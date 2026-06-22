"""Security policy primitives for built-in and custom tools.

This module provides the guardrails used by tool implementations to enforce
workspace boundaries, network restrictions, and tool authorization. It is
intentionally kept free of Textual or Rich dependencies so it can be unit tested
without the full UI stack.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
import socket
import urllib.parse
from enum import Enum, Flag, auto
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple


class Permission(Flag):
    """Permission categories used to classify tools."""

    READ = auto()
    WRITE = auto()
    EXECUTE = auto()
    NETWORK = auto()


class SecurityError(Exception):
    """Raised when a tool operation violates the active security policy."""


class OperationScope(Enum):
    """Risk classification for a single tool invocation."""

    INTERNAL = "internal"
    EXTERNAL = "external"
    SYSTEM = "system"
    DESTRUCTIVE = "destructive"


# Multi-level authorization tiers.
AUTHORIZATION_MANUAL = "manual"
AUTHORIZATION_AUTO = "auto"
AUTHORIZATION_YOLO = "yolo"
AUTHORIZATION_LEVELS = [AUTHORIZATION_MANUAL, AUTHORIZATION_AUTO, AUTHORIZATION_YOLO]


def is_authorized(level: str, scope: OperationScope) -> bool:
    """Return True if *level* permits *scope* without extra confirmation."""
    if level == AUTHORIZATION_YOLO:
        return True
    if scope in (OperationScope.EXTERNAL, OperationScope.SYSTEM, OperationScope.DESTRUCTIVE):
        return False
    return level == AUTHORIZATION_AUTO


class WorkspacePathPolicy:
    """Enforces that filesystem paths stay within a configured workspace root."""

    def __init__(self, root: Path, allow_absolute_outside: bool = False):
        self.root = Path(root).resolve()
        self.allow_absolute_outside = allow_absolute_outside

    def resolve(self, path: str | Path) -> Path:
        """Return an absolute Path for *path* relative to the workspace root.

        Raises:
            SecurityError: If the resolved path escapes the workspace and
                ``allow_absolute_outside`` is False.
        """
        candidate = Path(path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()

        if not self.allow_absolute_outside:
            try:
                resolved.relative_to(self.root)
            except ValueError as exc:
                raise SecurityError(
                    f"Path '{path}' resolves outside the workspace '{self.root}'."
                ) from exc

        return resolved

    def is_within_workspace(self, path: str | Path) -> bool:
        try:
            self.resolve(path)
            return True
        except SecurityError:
            return False

    def scope_for(self, path: str | Path) -> OperationScope:
        """Classify a path as INTERNAL or EXTERNAL relative to the workspace."""
        return OperationScope.INTERNAL if self.is_within_workspace(path) else OperationScope.EXTERNAL


class NetworkPolicy:
    """Restricts outbound HTTP requests by host and IP range."""

    # Hostnames that must always be treated as loopback/local.
    LOCALHOST_ALIASES = {"localhost", "localhost.localdomain"}

    def __init__(
        self,
        allow_hosts: Optional[Iterable[str]] = None,
        deny_hosts: Optional[Iterable[str]] = None,
        deny_private_loopback: bool = True,
    ):
        self.allow_hosts: Set[str] = {h.lower().rstrip(".") for h in (allow_hosts or [])}
        self.deny_hosts: Set[str] = {h.lower().rstrip(".") for h in (deny_hosts or [])}
        self.deny_private_loopback = deny_private_loopback

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Return a lowercased host with trailing root dot removed."""
        return host.lower().rstrip(".")

    @staticmethod
    def _ip_from_literal(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Parse an IP literal, including decimal/mixed IPv4 forms."""
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            pass
        # Accept dotted-decimal IPv4 (e.g. 2130706433 -> 127.0.0.1).
        try:
            decimal = int(host)
            if 0 <= decimal <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(decimal)
        except ValueError:
            pass
        return None

    def _check_ip(self, addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        """Raise SecurityError if the address is private/loopback/reserved/link-local."""
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_reserved
            or addr.is_link_local
            or addr.is_multicast
        ):
            raise SecurityError(
                f"IP address '{addr}' belongs to a private/loopback/reserved/link-local range."
            )

    def _resolve_host_ips(self, host: str) -> List[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Resolve a hostname to its IP addresses and validate them."""
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SecurityError(f"Could not resolve host '{host}': {exc}") from exc

        addresses: List[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        for info in infos:
            family, _, _, _, sockaddr = info
            ip_str = sockaddr[0]
            try:
                if family == socket.AF_INET:
                    addresses.append(ipaddress.IPv4Address(ip_str))
                elif family == socket.AF_INET6:
                    addresses.append(ipaddress.IPv6Address(ip_str))
            except ValueError:
                continue
        return addresses

    def _validate_host(self, host: str) -> Tuple[str, List[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
        """Return normalized host and resolved addresses, or raise SecurityError."""
        normalized = self._normalize_host(host)
        if not normalized:
            raise SecurityError("URL has no host.")

        if self.allow_hosts and normalized not in self.allow_hosts:
            raise SecurityError(f"Host '{host}' is not in the allow list.")

        if normalized in self.deny_hosts:
            raise SecurityError(f"Host '{host}' is in the deny list.")

        if self.deny_private_loopback and normalized in self.LOCALHOST_ALIASES:
            raise SecurityError(f"Host '{host}' resolves to a loopback alias.")

        # Try to parse as an IP literal first.
        literal_ip = self._ip_from_literal(normalized)
        if literal_ip is not None:
            if self.deny_private_loopback:
                self._check_ip(literal_ip)
            return normalized, [literal_ip]

        # Hostname: resolve and validate all returned addresses.
        addresses = self._resolve_host_ips(normalized)
        if self.deny_private_loopback:
            for addr in addresses:
                self._check_ip(addr)
        return normalized, addresses

    def validate_url(self, url: str) -> None:
        """Raise SecurityError if *url* is not allowed by this policy."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SecurityError(f"URL scheme '{parsed.scheme}' is not allowed.")

        host = parsed.hostname
        if not host:
            raise SecurityError("URL has no host.")

        self._validate_host(host)


# Tokens that indicate the command touches the system outside the workspace.
SYSTEM_COMMAND_KEYWORDS = {
    "apt", "apt-get", "yum", "dnf", "brew", "choco", "winget", "scoop",
    "pip install --system", "pip3 install --system", "npm install -g", "yarn global",
    "reg ", "reg.exe", "sc ", "sc.exe", "systemctl", "launchctl", "service ",
    "mkfs", "fdisk", "diskpart", "format ", "mount ", "umount ",
}

# Tokens that indicate destructive behavior.
DESTRUCTIVE_COMMAND_KEYWORDS = {
    "rm -rf", "rm -r -f", "rd /s", "rmdir /s", "del /s", "del /q", "erase /s",
    "format ", "mkfs", "dd if=", "> /dev/", "> /sys/", "> /proc/",
}


# Characters/substrings that indicate shell metacharacters or chaining. Any
# command containing these cannot be proven to stay inside the workspace without
# a real parser/sandbox, so it is escalated to at least SYSTEM.
SHELL_METACHARACTERS = re.compile(r"[|;<>\n\r]|\$\(|`|\$\{|\$[A-Za-z_]|\&\&|\&")


def _token_is_path(token: str) -> bool:
    """Return True if the token looks like a filesystem path candidate."""
    stripped = token.strip('"\'')
    if stripped.startswith("/") or stripped.startswith("~"):
        return True
    if len(stripped) >= 2 and stripped[1] == ":":
        return True
    return False


def classify_command_scope(command: str, policy: WorkspacePathPolicy) -> OperationScope:
    """Classify a shell command by its potential impact.

    This is a conservative heuristic, not a sandbox. Commands that cannot be
    proven to stay inside the workspace are escalated to SYSTEM or EXTERNAL.
    """
    cmd_lower = command.lower()

    # 1. Destructive patterns are highest risk.
    for keyword in DESTRUCTIVE_COMMAND_KEYWORDS:
        if keyword in cmd_lower:
            return OperationScope.DESTRUCTIVE

    # 2. System-level administration commands.
    for keyword in SYSTEM_COMMAND_KEYWORDS:
        if keyword in cmd_lower:
            return OperationScope.SYSTEM

    # 3. Any chaining/variable/redirection/pipe metacharacter means the command
    #    cannot be statically proven to stay inside the workspace.
    if SHELL_METACHARACTERS.search(command):
        return OperationScope.SYSTEM

    # 4. Path access: parse tokens with shlex to respect quoting, then check if
    #    any path resolves outside the workspace or uses parent-directory escape.
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        # Unbalanced quotes or other parsing issues -> cannot analyze safely.
        return OperationScope.SYSTEM

    for token in tokens:
        stripped = token.strip('"\'')
        if ".." in token or "%" in token or token.startswith("$"):
            return OperationScope.SYSTEM
        if _token_is_path(token):
            try:
                if not policy.is_within_workspace(stripped):
                    return OperationScope.EXTERNAL
            except Exception:
                continue

    return OperationScope.INTERNAL


# Python code snippets that touch the system outside the workspace.
PYTHON_SYSTEM_PATTERNS = [
    re.compile(r"\bos\.system\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\burllib\b"),
    re.compile(r"\burllib3\b"),
    re.compile(r"\brequests\b"),
    re.compile(r"\bhttpx\b"),
    re.compile(r"\bsocket\b"),
    re.compile(r"\bimportlib\b"),
    re.compile(r"\b__import__\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bgetattr\s*\("),
    re.compile(r"\bsetattr\s*\("),
]

# Destructive Python patterns.
PYTHON_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\bos\.remove\b"),
    re.compile(r"\bos\.rmdir\b"),
    re.compile(r"\bos\.unlink\b"),
    re.compile(r"\brm -rf\b"),
]

# File-access APIs that may write or read outside the workspace.
PYTHON_FILE_API_PATTERNS = [
    re.compile(r'\bopen\s*\(\s*["\']([^"\']+)["\']'),
    re.compile(r'\bpathlib\b'),
    re.compile(r'\bPath\s*\('),
    re.compile(r'\bio\.open\s*\('),
    re.compile(r'\bos\.open\s*\('),
    re.compile(r'\bfile\s*=\s*open\s*\('),
]


def classify_python_scope(code: str, policy: WorkspacePathPolicy) -> OperationScope:
    """Conservatively classify arbitrary Python code.

    Because the persistent REPL is not a real sandbox, any code that cannot be
    proven to stay inside the workspace is escalated to SYSTEM or EXTERNAL.
    """
    for pattern in PYTHON_DESTRUCTIVE_PATTERNS:
        if pattern.search(code):
            return OperationScope.DESTRUCTIVE
    for pattern in PYTHON_SYSTEM_PATTERNS:
        if pattern.search(code):
            return OperationScope.SYSTEM
    # If code uses any file API, conservatively treat it as EXTERNAL unless we
    # can prove all literal paths stay inside the workspace.
    has_file_api = False
    for pattern in PYTHON_FILE_API_PATTERNS:
        if pattern.search(code):
            has_file_api = True
            break
    if has_file_api:
        return OperationScope.EXTERNAL
    return OperationScope.INTERNAL


class CommandPolicy:
    """Minimal sandbox policy for persistent shell execution."""

    # Characters/substrings that commonly chain or redirect commands. This is a
    # coarse guard; it is not a substitute for a real sandbox.
    DANGEROUS_PATTERNS = re.compile(r"[;|<>]|\$\(|`|\n|\r|\&\&|\&")

    def __init__(
        self,
        allow_patterns: Optional[Iterable[str]] = None,
        deny_patterns: Optional[Iterable[str]] = None,
        require_confirmation_for_chained: bool = True,
    ):
        self.allow_patterns = [re.compile(p) for p in (allow_patterns or [])]
        self.deny_patterns = [re.compile(p) for p in (deny_patterns or [])]
        self.require_confirmation_for_chained = require_confirmation_for_chained

    def classify(self, command: str) -> tuple[bool, Optional[str]]:
        """Return (allowed, reason)."""
        for pattern in self.deny_patterns:
            if pattern.search(command):
                return False, f"matches deny pattern: {pattern.pattern}"

        if self.allow_patterns and not any(p.search(command) for p in self.allow_patterns):
            return False, "does not match any allow pattern"

        if self.require_confirmation_for_chained and self.DANGEROUS_PATTERNS.search(command):
            return False, "contains shell chaining metacharacters"

        return True, None
