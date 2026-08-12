"""Immutable UI state model; never imports Textual or kernel services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.lifecycle import KernelStatus


class OverlayKind(str, Enum):
    """Modal overlays; exactly one may be open at a time."""

    CONNECT = "connect"
    COMMANDS = "commands"
    SESSIONS = "sessions"
    MODELS = "models"
    APPROVAL = "approval"
    PLAN = "plan"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class AppState:
    """Read-only view state; no secrets, no mutable collections, no widgets."""

    kernel_status: KernelStatus | None = None
    active_session_id: SessionId | None = None
    workspace_label: str = ""
    model_label: str = "Not connected"
    draft: str = ""
    pending_draft: str | None = None
    active_turn_id: TurnId | None = None
    overlay: OverlayKind | None = None
    sidebar_visible: bool = False
    leader_active: bool = False
    shutting_down: bool = False
