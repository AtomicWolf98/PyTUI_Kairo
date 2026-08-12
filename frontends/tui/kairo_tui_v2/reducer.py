"""Pure UI state transitions; no Textual imports, fully replayable."""

from __future__ import annotations

from dataclasses import dataclass, replace

from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.lifecycle import KernelStatus

from kairo_tui_v2.state import AppState


@dataclass(frozen=True)
class DraftChanged:
    """The composer text changed; mirrors the draft."""

    text: str


@dataclass(frozen=True)
class SubmitDraft:
    """Enter was pressed; the draft is preserved until the kernel accepts."""

    text: str


@dataclass(frozen=True)
class DraftAccepted:
    """The controller confirmed the turn; only then may the draft clear."""

    session_id: SessionId | None = None


@dataclass(frozen=True)
class DraftRejected:
    """Submission failed; the draft stays and the composer must refocus."""

    notice: str = ""


@dataclass(frozen=True)
class KernelStatusChanged:
    status: KernelStatus


UiAction = DraftChanged | SubmitDraft | DraftAccepted | DraftRejected | KernelStatusChanged


def reduce(state: AppState, action: UiAction) -> AppState:
    """Apply one action; unknown actions are no-ops for forward compatibility."""
    if isinstance(action, DraftChanged):
        return replace(state, draft=action.text)
    if isinstance(action, SubmitDraft):
        # Never clear here: the controller clears via DraftAccepted only after
        # the kernel accepts the turn (C1). Pending drafts are a P1 concern.
        return state
    if isinstance(action, DraftAccepted):
        return replace(state, draft="")
    if isinstance(action, DraftRejected):
        draft = state.pending_draft if state.pending_draft is not None else state.draft
        return replace(state, draft=draft)
    if isinstance(action, KernelStatusChanged):
        return replace(state, kernel_status=action.status)
    return state
