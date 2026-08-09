"""Pure session-row view-model for the Sessions page.

Textual-free: the Sessions screen maps each SessionRow to a row Button 1:1
and never re-derives ordering or badges from the store.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairo_tui.store import AppState


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    name: str
    message_count: int
    updated_at: str
    running: bool
    active: bool


def session_rows(state: AppState, text: str = "") -> tuple[SessionRow, ...]:
    """Sessions newest-updated first, with running/active flags (badges).

    ``text`` filters the store list locally by session name for instant
    feedback; the kernel ``search`` (which also matches message content) stays
    a kernel command and is not duplicated here.
    """
    needle = text.strip().casefold()
    running = frozenset(str(turn.session_id) for turn in state.active_turns)
    active = state.active_session_id
    ordered = sorted(state.sessions, key=lambda s: s.updated_at, reverse=True)
    return tuple(
        SessionRow(
            str(s.session_id), s.name, s.message_count,
            s.updated_at.isoformat(timespec="seconds"),
            str(s.session_id) in running, str(s.session_id) == active,
        )
        for s in ordered
        if not needle or needle in s.name.casefold()
    )


def running_session_ids(state: AppState) -> frozenset[str]:
    return frozenset(str(turn.session_id) for turn in state.active_turns)
