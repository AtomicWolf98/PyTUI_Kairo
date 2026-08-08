"""Tool descriptions and typed execution results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.content import ContentBlock
from kairo_kernel.contracts.enums import OperationScope, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import Contract, JsonObject


@dataclass(frozen=True)
class ToolDescriptor(Contract):
    name: str
    description: str
    parameters_schema: JsonObject
    permissions: tuple[str, ...]
    source: str = "builtin"
    manifest_digest: str = ""


@dataclass(frozen=True)
class ToolInvocation(Contract):
    tool_call_id: ToolCallId
    turn_id: TurnId
    session_id: SessionId
    name: str
    arguments: JsonObject
    scope: OperationScope


@dataclass(frozen=True)
class ToolExecutionContext(Contract):
    workspace_root: str
    authorization_mode: str
    environment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolOutputChunk(Contract):
    tool_call_id: ToolCallId
    sequence: int
    content: tuple[ContentBlock, ...]


@dataclass(frozen=True)
class ToolResult(Contract):
    tool_call_id: ToolCallId
    name: str
    status: ToolExecutionStatus
    content: tuple[ContentBlock, ...]
    started_at: datetime
    finished_at: datetime
    error_message: str = ""

