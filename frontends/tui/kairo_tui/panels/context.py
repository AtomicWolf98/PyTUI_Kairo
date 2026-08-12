"""Context sidebar: session/model/usage/turn/interaction/workspace summary."""

from __future__ import annotations

from textual.widgets import Static

from kairo_tui.state import AppState


class ContextPanel(Static):
    """Read-only context view; every value comes from the immutable state."""

    def render_state(self, state: AppState) -> None:
        lines = ["[b]Context[/b]"]
        session = next(
            (item for item in state.sessions if item.session_id == state.active_session_id),
            None,
        )
        lines.append(f"Session: {session.name if session is not None else '—'}")
        lines.append(f"Model: {state.profile_label or state.model_label}")
        turn = next((item for item in state.turns if not item.terminal), None)
        phase = turn.phase.value if turn is not None and turn.phase is not None else "idle"
        lines.append(f"Turn: {phase}")
        for _turn_id, stats in state.usage:
            used = getattr(stats, "used_tokens", 0)
            window = getattr(stats, "context_window", 0)
            lines.append(f"Context: {used}/{window} tokens")
        interactions = len(state.pending_interactions)
        lines.append(f"Pending interactions: {interactions}")
        workspace = state.workspace
        if workspace is not None:
            lines.append(f"Workspace revision: {workspace.revision}")
        self.update("\n".join(lines))
