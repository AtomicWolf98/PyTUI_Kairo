"""Repository, memory, secret, resource, prompt and telemetry DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from kairo_kernel.contracts.content import ContentBlock, Message
from kairo_kernel.contracts.identifiers import MemoryId, ResourceId, SecretId, SessionId, SpanId, TraceId
from kairo_kernel.contracts.json import Contract, JsonObject


@dataclass(frozen=True)
class SessionRecord(Contract):
    session_id: SessionId
    name: str
    messages: tuple[Message, ...]
    created_at: datetime
    updated_at: datetime
    compression_count: int = 0


@dataclass(frozen=True)
class SessionSummary(Contract):
    session_id: SessionId
    name: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    context_used_tokens: int = 0


@dataclass(frozen=True)
class ConfigSnapshot(Contract):
    revision: int
    values: JsonObject
    redacted: bool = True


@dataclass(frozen=True)
class WorkspaceRecord(Contract):
    root: str
    revision: int
    previous_root: str = ""


@dataclass(frozen=True)
class MemoryEntry(Contract):
    memory_id: MemoryId
    namespace: str
    key: str
    content: tuple[ContentBlock, ...]
    created_at: datetime
    updated_at: datetime
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryQuery(Contract):
    namespace: str
    text: str
    limit: int = 20
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretDescriptor(Contract):
    secret_id: SecretId
    source: str
    masked_value: str
    present: bool


@dataclass(frozen=True)
class SecretInput(Contract):
    secret_id: SecretId
    value: str = field(repr=False, compare=False, metadata={"secret": True})


@dataclass(frozen=True)
class ResourceDescriptor(Contract):
    resource_id: ResourceId
    uri: str
    name: str
    media_type: str
    size_bytes: int | None = None
    sha256: str = ""
    metadata: JsonObject = JsonObject()


@dataclass(frozen=True)
class ResourceRead(Contract):
    descriptor: ResourceDescriptor
    content: tuple[ContentBlock, ...]


@dataclass(frozen=True)
class PromptDescriptor(Contract):
    name: str
    description: str
    arguments_schema: JsonObject = JsonObject()


@dataclass(frozen=True)
class PromptRenderRequest(Contract):
    name: str
    arguments: JsonObject = JsonObject()


@dataclass(frozen=True)
class PromptRenderResult(Contract):
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class TraceContext(Contract):
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None


@dataclass(frozen=True)
class LogRecord(Contract):
    timestamp: datetime
    level: str
    message: str
    fields: JsonObject = JsonObject()
    trace: TraceContext | None = None


@dataclass(frozen=True)
class MetricRecord(Contract):
    name: str
    value: float
    timestamp: datetime
    unit: str = ""
    attributes: JsonObject = JsonObject()


@dataclass(frozen=True)
class SpanRecord(Contract):
    context: TraceContext
    name: str
    started_at: datetime
    finished_at: datetime | None = None
    attributes: JsonObject = JsonObject()

