"""Public, deterministic kernel capability matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Capability:
    name: str
    area: str
    status: str
    operations: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    visibility: str = "public"
    source: str = "kernel"


@dataclass(frozen=True)
class CapabilityMatrix:
    version: str
    capabilities: tuple[Capability, ...]

    def public(self) -> CapabilityMatrix:
        return CapabilityMatrix(self.version, tuple(item for item in self.capabilities if item.visibility == "public"))

    def get(self, name: str) -> Capability | None:
        return next((item for item in self.capabilities if item.name == name), None)

    def supports(self, name: str, operation: str = "") -> bool:
        item = self.get(name)
        return item is not None and item.status == "available" and (not operation or operation in item.operations)


class CapabilityReporterPort(Protocol):
    async def capabilities(self) -> tuple[Capability, ...]: ...


class CapabilityService:
    def __init__(
        self,
        reporters: tuple[CapabilityReporterPort, ...] = (),
        *,
        version: str = "1",
        baseline: tuple[Capability, ...] | None = None,
    ):
        self.reporters = reporters
        self.version = version
        self.baseline = baseline if baseline is not None else default_capabilities()

    async def snapshot(self, *, public_only: bool = True) -> CapabilityMatrix:
        capabilities = list(self.baseline)
        for reporter in self.reporters:
            capabilities.extend(await reporter.capabilities())
        normalized = _normalize(capabilities)
        matrix = CapabilityMatrix(self.version, normalized)
        return matrix.public() if public_only else matrix


def default_capabilities() -> tuple[Capability, ...]:
    return (
        Capability("turns", "agent", "available", ("run", "cancel", "status", "events")),
        Capability("interactions", "agent", "available", ("approve", "reject", "expire")),
        Capability("sessions", "persistence", "available", ("create", "list", "read", "rename", "delete")),
        Capability("workspace", "workspace", "available", ("inspect", "switch", "snapshot")),
        Capability("providers", "integration", "available", ("resolve", "stream", "probe")),
        Capability("tools", "extension", "available", ("list", "classify", "execute", "reload")),
        Capability("skills", "extension", "available", ("inspect", "trust", "reload", "revoke")),
        Capability("mcp", "integration", "available", ("connect", "catalog", "call", "reconnect", "close")),
        Capability("memory", "persistence", "available", ("search", "get", "put", "delete")),
        Capability("resources", "context", "available", ("list", "read")),
        Capability("prompts", "context", "available", ("list", "render")),
        Capability("diagnostics", "operations", "available", ("local", "full")),
        Capability("observability", "operations", "available", ("log", "metric", "span")),
    )


def _normalize(capabilities: list[Capability]) -> tuple[Capability, ...]:
    by_name: dict[str, Capability] = {}
    for item in capabilities:
        if not item.name or item.status not in {"available", "degraded", "unavailable"}:
            raise ValueError(f"Invalid capability: {item.name!r}")
        if item.visibility not in {"public", "internal"}:
            raise ValueError(f"Invalid capability visibility: {item.visibility!r}")
        if item.name in by_name:
            raise ValueError(f"Duplicate capability name: {item.name}")
        by_name[item.name] = item
    return tuple(sorted(by_name.values(), key=lambda item: (item.area, item.name)))
