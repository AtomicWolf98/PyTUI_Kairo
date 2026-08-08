"""Turn submission, state and completion contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.content import Message
from kairo_kernel.contracts.enums import TurnPhase, TurnStatus
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.json import Contract, JsonObject


@dataclass(frozen=True)
class TurnRequest(Contract):
    text: str
    session_id: SessionId | None = None
    client_request_id: str = ""
    metadata: JsonObject = JsonObject()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Turn text must not be empty.")


@dataclass(frozen=True)
class TurnAccepted(Contract):
    turn_id: TurnId
    session_id: SessionId
    accepted_at: datetime


@dataclass(frozen=True)
class TurnSnapshot(Contract):
    turn_id: TurnId
    session_id: SessionId
    status: TurnStatus
    phase: TurnPhase | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool = False


@dataclass(frozen=True)
class TurnResult(Contract):
    turn_id: TurnId
    session_id: SessionId
    status: TurnStatus
    messages: tuple[Message, ...]
    started_at: datetime
    finished_at: datetime
    error_message: str = ""

    def __post_init__(self) -> None:
        if self.status not in (TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED):
            raise ValueError("TurnResult status must be terminal.")


@dataclass(frozen=True)
class CancelReceipt(Contract):
    turn_id: TurnId
    requested: bool
    already_terminal: bool = False

