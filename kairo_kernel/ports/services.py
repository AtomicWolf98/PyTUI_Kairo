"""Memory, secret, resource, prompt and observability ports."""

from __future__ import annotations

from typing import Protocol

from kairo_kernel.contracts.identifiers import MemoryId, ResourceId, SecretId
from kairo_kernel.contracts.support import (
    LogRecord,
    MemoryEntry,
    MemoryQuery,
    MetricRecord,
    PromptDescriptor,
    PromptRenderRequest,
    PromptRenderResult,
    ResourceDescriptor,
    ResourceRead,
    SecretDescriptor,
    SecretInput,
    SpanRecord,
)
from kairo_kernel.errors import KernelResult


class MemoryPort(Protocol):
    async def search(self, query: MemoryQuery) -> tuple[MemoryEntry, ...]: ...

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]: ...

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]: ...

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]: ...


class SecretPort(Protocol):
    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]: ...

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]: ...

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]: ...

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]: ...


class ResourcePort(Protocol):
    async def list(self) -> tuple[ResourceDescriptor, ...]: ...

    async def read(self, resource_id: ResourceId) -> KernelResult[ResourceRead]: ...


class PromptPort(Protocol):
    async def list(self) -> tuple[PromptDescriptor, ...]: ...

    async def render(self, request: PromptRenderRequest) -> KernelResult[PromptRenderResult]: ...


class ObservabilityPort(Protocol):
    async def log(self, record: LogRecord) -> None: ...

    async def metric(self, record: MetricRecord) -> None: ...

    async def span(self, record: SpanRecord) -> None: ...

