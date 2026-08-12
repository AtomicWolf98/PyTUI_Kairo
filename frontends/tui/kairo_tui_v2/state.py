"""Immutable UI state model; never imports Textual or kernel services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from kairo_kernel.contracts.content import ContentBlock
from kairo_kernel.contracts.enums import TurnPhase, TurnStatus
from kairo_kernel.contracts.identifiers import MessageId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionRequest
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
class SessionView:
    """Immutable session summary; running reflects an active turn."""

    session_id: SessionId
    name: str
    message_count: int = 0
    updated_at: datetime | None = None
    running: bool = False


@dataclass(frozen=True)
class TurnView:
    """Per-turn status; terminal is set exactly once per turn."""

    turn_id: TurnId
    session_id: SessionId
    status: TurnStatus
    phase: TurnPhase | None = None
    terminal: bool = False


@dataclass(frozen=True)
class TranscriptEntry:
    """One message inside a session transcript."""

    message_id: MessageId
    role: str
    kind: str
    content: tuple[ContentBlock, ...] = ()
    name: str = ""


@dataclass(frozen=True)
class SessionTranscript:
    """Per-session transcript; sessions are never mixed."""

    session_id: SessionId
    entries: tuple[TranscriptEntry, ...] = ()


@dataclass(frozen=True)
class WorkspaceView:
    root: str
    revision: int


@dataclass(frozen=True)
class ToolCardView:
    """One tool invocation's lifecycle; updated by TOOL events."""

    tool_call_id: str
    name: str
    status: str = "requested"  # requested -> started -> running -> completed
    arguments: object = None  # JsonObject
    output: tuple[ContentBlock, ...] = ()
    result_status: str = ""
    error: str = ""


@dataclass(frozen=True)
class PlanCardView:
    """Structured plan card; never mixed into ordinary content."""

    session_id: SessionId
    turn_id: TurnId
    message_id: MessageId
    title: str = "Plan"
    blocks: tuple[ContentBlock, ...] = ()
    instructions: str = ""


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
    # C0: kernel-fed view state.
    sessions: tuple[SessionView, ...] = ()
    transcripts: tuple[SessionTranscript, ...] = ()
    turns: tuple[TurnView, ...] = ()
    pending_interactions: tuple[InteractionRequest, ...] = ()
    last_sequence: int = 0
    workspace: WorkspaceView | None = None
    profile_label: str = ""
    notice: str = ""
    # C1: chat workflow view state.
    tool_cards: tuple[ToolCardView, ...] = ()
    plan_cards: tuple[PlanCardView, ...] = ()
    usage: tuple[tuple[TurnId, object], ...] = ()  # (turn_id, ContextStats) once per turn
    stopping_turn_id: TurnId | None = None
