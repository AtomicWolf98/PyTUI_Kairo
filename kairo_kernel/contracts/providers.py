"""Provider-neutral LLM request and streaming contracts."""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.content import ContentBlock, Message, ToolCallBlock
from kairo_kernel.contracts.enums import ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import Contract
from kairo_kernel.contracts.tools import ToolDescriptor


@dataclass(frozen=True)
class ProviderProfile(Contract):
    profile_id: ProfileId
    label: str
    provider: str
    model: str
    base_url: str
    context_window: int
    max_output_tokens: int
    temperature: float
    secret_id: str = ""


@dataclass(frozen=True)
class ProviderRequest(Contract):
    profile: ProviderProfile
    messages: tuple[Message, ...]
    tools: tuple[ToolDescriptor, ...] = ()
    max_output_tokens: int | None = None
    temperature: float | None = None
    role: str = "chat"


@dataclass(frozen=True)
class ProviderUsage(Contract):
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


@dataclass(frozen=True)
class ProviderFailure(Contract):
    kind: ProviderFailureKind
    message: str
    retryable: bool
    status_code: int | None = None


@dataclass(frozen=True)
class ProviderStreamEvent(Contract):
    kind: ProviderStreamKind
    content: tuple[ContentBlock, ...] = ()
    tool_call: ToolCallBlock | None = None
    usage: ProviderUsage | None = None
    failure: ProviderFailure | None = None
    finish_reason: str = ""

    def __post_init__(self) -> None:
        if self.kind is ProviderStreamKind.TOOL_CALL and self.tool_call is None:
            raise ValueError("Tool-call events require tool_call.")
        if self.kind is ProviderStreamKind.USAGE and self.usage is None:
            raise ValueError("Usage events require usage.")
        if self.kind is ProviderStreamKind.FAILED and self.failure is None:
            raise ValueError("Failed events require failure.")
