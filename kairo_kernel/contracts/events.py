"""Versioned event envelope and typed event payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.content import ContentBlock
from kairo_kernel.contracts.enums import EventType, LifecycleState, TurnPhase, TurnStatus
from kairo_kernel.contracts.identifiers import EventId, InteractionId, KernelId, MessageId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from kairo_kernel.contracts.json import Contract, JsonObject
from kairo_kernel.contracts.lifecycle import EVENT_SCHEMA_VERSION, ContextStats
from kairo_kernel.contracts.tools import ToolInvocation, ToolOutputChunk, ToolResult


@dataclass(frozen=True)
class LifecycleEvent(Contract):
    state: LifecycleState
    reason: str = ""


@dataclass(frozen=True)
class TurnEvent(Contract):
    status: TurnStatus
    phase: TurnPhase | None = None
    reason: str = ""


@dataclass(frozen=True)
class MessageEvent(Contract):
    message_id: MessageId
    action: str
    content: tuple[ContentBlock, ...] = ()


@dataclass(frozen=True)
class ToolEvent(Contract):
    action: str
    invocation: ToolInvocation | None = None
    output: ToolOutputChunk | None = None
    result: ToolResult | None = None


@dataclass(frozen=True)
class InteractionEvent(Contract):
    action: str
    request: InteractionRequest | None = None
    response: InteractionResponse | None = None
    interaction_id: InteractionId | None = None


@dataclass(frozen=True)
class UsageEvent(Contract):
    context: ContextStats


@dataclass(frozen=True)
class ChangeEvent(Contract):
    revision: int
    subject_id: str = ""
    summary: str = ""


@dataclass(frozen=True)
class NoticeEvent(Contract):
    level: str
    message: str
    details: JsonObject = JsonObject()


EventPayload = LifecycleEvent | TurnEvent | MessageEvent | ToolEvent | InteractionEvent | UsageEvent | ChangeEvent | NoticeEvent


@dataclass(frozen=True)
class KernelEvent(Contract):
    event_id: EventId
    kernel_id: KernelId
    sequence: int
    timestamp: datetime
    event_type: EventType
    payload: EventPayload
    schema_version: int = EVENT_SCHEMA_VERSION
    turn_sequence: int | None = None
    turn_id: TurnId | None = None
    session_id: SessionId | None = None
    workspace_revision: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("Unsupported event schema version.")
        if self.sequence < 1:
            raise ValueError("Event sequence must be positive.")


@dataclass(frozen=True)
class EventReplay(Contract):
    events: tuple[KernelEvent, ...]
    oldest_sequence: int
    newest_sequence: int
    gap: bool = False
