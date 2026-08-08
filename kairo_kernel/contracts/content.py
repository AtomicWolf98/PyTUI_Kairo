"""Conversation messages and multimodal content blocks."""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.enums import MessageKind, MessageRole, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import MessageId, ResourceId, ToolCallId
from kairo_kernel.contracts.json import Contract, JsonObject


@dataclass(frozen=True)
class TextBlock(Contract):
    text: str


@dataclass(frozen=True)
class ReasoningBlock(Contract):
    text: str
    redacted: bool = False


@dataclass(frozen=True)
class ImageBlock(Contract):
    media_type: str
    uri: str = ""
    base64_data: str = ""
    alt_text: str = ""


@dataclass(frozen=True)
class AudioBlock(Contract):
    media_type: str
    uri: str = ""
    base64_data: str = ""
    transcript: str = ""


@dataclass(frozen=True)
class FileBlock(Contract):
    name: str
    media_type: str
    uri: str
    size_bytes: int | None = None
    sha256: str = ""


@dataclass(frozen=True)
class ResourceBlock(Contract):
    resource_id: ResourceId
    uri: str
    name: str = ""
    description: str = ""
    media_type: str = "application/octet-stream"


@dataclass(frozen=True)
class ToolCallBlock(Contract):
    tool_call_id: ToolCallId
    name: str
    arguments: JsonObject = JsonObject()


@dataclass(frozen=True)
class ToolResultBlock(Contract):
    tool_call_id: ToolCallId
    name: str
    status: ToolExecutionStatus
    content: tuple[ContentBlock, ...] = ()


ContentBlock = TextBlock | ReasoningBlock | ImageBlock | AudioBlock | FileBlock | ResourceBlock | ToolCallBlock | ToolResultBlock


@dataclass(frozen=True)
class Message(Contract):
    message_id: MessageId
    role: MessageRole
    kind: MessageKind
    content: tuple[ContentBlock, ...]
    name: str = ""
