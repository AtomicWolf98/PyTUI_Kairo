"""Implementation models captured once when a turn is accepted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import ProfileId, SessionId, TurnId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.contracts.tools import ToolDescriptor


@dataclass(frozen=True)
class EngineOptions:
    default_session_id: SessionId | None = None
    profile_id: ProfileId | None = None
    workspace_root: str = ""
    workspace_revision: int = 0
    authorization_mode: AuthorizationMode = AuthorizationMode.MANUAL
    plan_mode: bool = False
    thinking_mode: bool = True
    context_trigger_percent: float = 85.0
    context_target_percent: float = 60.0
    preserve_recent_turns: int = 4
    interaction_timeout_seconds: float = 3600.0
    max_tool_rounds: int = 32
    stop_saves_partial: bool = True


@dataclass(frozen=True)
class RunSnapshot:
    turn_id: TurnId
    session_id: SessionId
    session: SessionRecord
    profile: ProviderProfile
    tools: tuple[ToolDescriptor, ...]
    options: EngineOptions
    accepted_at: datetime
