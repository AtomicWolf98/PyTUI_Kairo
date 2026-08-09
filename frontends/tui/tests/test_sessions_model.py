"""Session-row view-model: newest-first ordering, running badge, active flag."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.enums import TurnPhase, TurnStatus
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.support import SessionSummary
from kairo_kernel.contracts.turns import ActiveTurn

from kairo_tui.sessions_model import running_session_ids, session_rows
from kairo_tui.store import AppState


def _summary(session_id: str, name: str, updated_at: datetime, message_count: int = 0) -> SessionSummary:
    return SessionSummary(
        SessionId(session_id), name, message_count,
        datetime(2026, 1, 1, tzinfo=timezone.utc), updated_at,
    )


def test_session_rows_newest_updated_first() -> None:
    state = AppState(
        sessions=(
            _summary("old", "Old", datetime(2026, 1, 3, tzinfo=timezone.utc)),
            _summary("new", "New", datetime(2026, 1, 5, tzinfo=timezone.utc)),
            _summary("mid", "Mid", datetime(2026, 1, 4, tzinfo=timezone.utc)),
        )
    )
    rows = session_rows(state)
    assert [row.session_id for row in rows] == ["new", "mid", "old"]
    assert rows[0].updated_at == "2026-01-05T00:00:00+00:00"


def test_running_badge_comes_from_active_turns() -> None:
    state = AppState(
        sessions=(
            _summary("s1", "One", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _summary("s2", "Two", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ),
        active_turns=(ActiveTurn(TurnId("t1"), SessionId("s2"), TurnStatus.RUNNING, TurnPhase.STREAMING),),
    )
    rows = session_rows(state)
    by_id = {row.session_id: row for row in rows}
    assert by_id["s1"].running is False
    assert by_id["s2"].running is True
    assert running_session_ids(state) == frozenset({"s2"})


def test_active_flag_comes_from_active_session_id() -> None:
    state = AppState(
        sessions=(
            _summary("s1", "One", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _summary("s2", "Two", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ),
        active_session_id="s2",
    )
    rows = session_rows(state)
    by_id = {row.session_id: row for row in rows}
    assert by_id["s1"].active is False
    assert by_id["s2"].active is True
