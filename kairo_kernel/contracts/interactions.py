"""Frontend-neutral human interaction contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kairo_kernel.contracts.enums import InteractionAction, InteractionKind
from kairo_kernel.contracts.identifiers import InteractionId, SessionId, TurnId
from kairo_kernel.contracts.json import Contract


@dataclass(frozen=True)
class InteractionChoice(Contract):
    action: InteractionAction
    label: str
    destructive: bool = False


@dataclass(frozen=True)
class InteractionRequest(Contract):
    interaction_id: InteractionId
    turn_id: TurnId
    session_id: SessionId
    kind: InteractionKind
    prompt: str
    choices: tuple[InteractionChoice, ...]
    expires_at: datetime | None
    safe_default: InteractionAction

    def __post_init__(self) -> None:
        actions = {choice.action for choice in self.choices}
        if self.safe_default not in actions:
            raise ValueError("safe_default must be one of the offered choices.")
        if self.safe_default in (InteractionAction.APPROVE_ONCE, InteractionAction.ENABLE_AUTO, InteractionAction.ENABLE_YOLO):
            raise ValueError("safe_default must fail closed.")


@dataclass(frozen=True)
class InteractionResponse(Contract):
    interaction_id: InteractionId
    turn_id: TurnId
    action: InteractionAction
    text: str = ""


@dataclass(frozen=True)
class InteractionReceipt(Contract):
    interaction_id: InteractionId
    turn_id: TurnId
    accepted: bool

