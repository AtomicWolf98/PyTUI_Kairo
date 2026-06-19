"""Security policy primitives for built-in and custom tools.

This module provides the guardrails used by tool implementations to enforce
workspace boundaries, network restrictions, and tool authorization. It is
intentionally kept free of Textual or Rich dependencies so it can be unit tested
without the full UI stack.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
from enum import Enum, Flag, auto
from pathlib import Path
from typing import Iterable, Optional, Set


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

    def __init__(
        self,
        allow_hosts: Optional[Iterable[str]] = None,
        deny_hosts: Optional[Iterable[str]] = None,
        deny_private_loopback: bool = True,
    ):
        self.allow_hosts: Set[str] = {h.lower() for h in (allow_hosts or [])}
        self.deny_hosts: Set[str] = {h.lower() for h in (deny_hosts or [])}
        self.deny_private_loopback = deny_private_loopback

    def validate_url(self, url: str) -> None:
        """Raise SecurityError if *url* is not allowed by this policy."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SecurityError(f"URL scheme '{parsed.scheme}' is not allowed.")

        host = (parsed.hostname or "").lower()
        if not host:
            raise SecurityError("URL has no host.")

        if self.allow_hosts and host not in self.allow_hosts:
            raise SecurityError(f"Host '{host}' is not in the allow list.")

        if host in self.deny_hosts:
            raise SecurityError(f"Host '{host}' is in the deny list.")

        if self.deny_private_loopback:
            try:
                addr = ipaddress.ip_address(host)
            except ValueError:
                # hostname; resolve and check is done at fetch time by the tool
                return
            if addr.is_private or addr.is_loopback or addr.is_reserved:
                raise SecurityError(
                    f"IP address '{host}' belongs to a private/loopback/reserved range."
                )


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


def classify_command_scope(command: str, policy: WorkspacePathPolicy) -> OperationScope:
    """Classify a shell command by its potential impact."""
    cmd_lower = command.lower()

    # 1. Destructive patterns are highest risk.
    for keyword in DESTRUCTIVE_COMMAND_KEYWORDS:
        if keyword in cmd_lower:
            return OperationScope.DESTRUCTIVE

    # 2. System-level administration commands.
    for keyword in SYSTEM_COMMAND_KEYWORDS:
        if keyword in cmd_lower:
            return OperationScope.SYSTEM

    # 3. External path access (coarse check for cd/cp/mv/cat target paths).
    # We look for absolute paths or relative paths that resolve outside the workspace.
    for token in command.replace(";", " ").replace("|", " ").replace("&&", " ").split():
        if token.startswith("/") or token.startswith("~") or (len(token) > 2 and token[1] == ":"):
            try:
                if not policy.is_within_workspace(token):
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
]

# Destructive Python patterns.
PYTHON_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\bos\.remove\b"),
    re.compile(r"\bos\.rmdir\b"),
    re.compile(r"\bos\.unlink\b"),
    re.compile(r"\brm -rf\b"),
]


def classify_python_scope(code: str, policy: WorkspacePathPolicy) -> OperationScope:
    """Conservatively classify arbitrary Python code."""
    for pattern in PYTHON_DESTRUCTIVE_PATTERNS:
        if pattern.search(code):
            return OperationScope.DESTRUCTIVE
    for pattern in PYTHON_SYSTEM_PATTERNS:
        if pattern.search(code):
            return OperationScope.SYSTEM
    # If code opens files outside the workspace, treat as external.
    for match in re.finditer(r'open\s*\(\s*["\']([^"\']+)["\']', code):
        path = match.group(1)
        try:
            if not policy.is_within_workspace(path):
                return OperationScope.EXTERNAL
        except Exception:
            continue
    return OperationScope.INTERNAL


class CommandPolicy:
    """Minimal sandbox policy for persistent shell execution."""

    # Characters/substrings that commonly chain commands. This is a coarse guard;
    # it is not a substitute for a real sandbox.
    DANGEROUS_PATTERNS = re.compile(r"[;&]|\$\(|`|\b\s*\n\s*\b")

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
