"""Provider-neutral LLM request and streaming contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from kairo_kernel.contracts.content import ContentBlock, Message, ToolCallBlock
from kairo_kernel.contracts.enums import ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import Contract
from kairo_kernel.contracts.support import SecretInput
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


@dataclass(frozen=True)
class ProviderConnectionRequest:
    """Atomic provider connection command; in-process only, never serialized.

    Carries the full profile plus an optional secret input. A profile with a
    secret reference must be accompanied by the matching ``SecretInput``. This
    type intentionally does not inherit ``Contract``: it is a command object
    with in-process semantics, and the secret value never appears in repr,
    JSON, events or logs (``secret`` is ``repr=False`` / ``compare=False``).
    """

    profile: ProviderProfile
    secret: SecretInput | None = field(default=None, repr=False, compare=False)
    role: str = "chat"
    make_default: bool = True
    expected_revision: int = 0


@dataclass(frozen=True)
class ProviderConnectionReceipt(Contract):
    """Outcome of a successful atomic provider connection."""

    profile_id: ProfileId
    role: str
    catalog_revision: int
    default_profile_id: ProfileId | None
