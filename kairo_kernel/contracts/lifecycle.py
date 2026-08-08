"""Kernel lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.enums import AuthorizationMode, LifecycleState
from kairo_kernel.contracts.identifiers import KernelId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.json import Contract

KERNEL_API_VERSION = "1.0"
EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class KernelCapabilities(Contract):
    api_version: str = KERNEL_API_VERSION
    event_schema_version: int = EVENT_SCHEMA_VERSION
    content_types: tuple[str, ...] = ("text", "reasoning", "image", "audio", "file", "resource", "tool_call", "tool_result")
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextStats(Contract):
    used_tokens: int
    context_window: int
    percent: float
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class KernelStatus(Contract):
    kernel_id: KernelId
    state: LifecycleState
    package_version: str
    started_at: datetime | None
    workspace_root: str
    workspace_revision: int
    active_profile_id: ProfileId | None
    active_session_id: SessionId | None
    active_turn_id: TurnId | None
    authorization_mode: AuthorizationMode
    plan_mode: bool
    thinking_mode: bool
    context: ContextStats
    degraded_reason: str = ""


@dataclass(frozen=True)
class ShutdownRequest(Contract):
    grace_period_seconds: float = 5.0
    cancel_active_turn: bool = True


@dataclass(frozen=True)
class ShutdownReport(Contract):
    state: LifecycleState
    active_turn_cancelled: bool
    resources_closed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

