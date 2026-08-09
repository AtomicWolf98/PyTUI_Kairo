"""Shared TUI widgets."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static, TextArea


class TopBar(Static):
    """Kernel/workspace/profile/authorization status line (store-driven)."""

    def render_status(self, state) -> None:
        status = state.kernel_status
        if status is None:
            self.update("Kairo — starting…")
            return
        auth = status.authorization_mode.value
        plan = "Plan" if status.plan_mode else "plan-off"
        think = "Think" if status.thinking_mode else "think-off"
        turns = len(state.active_turns)
        compat = " — [b]compat mode (minimal layout)[/b]" if state.compat_mode else ""
        self.update(
            f"Kairo {status.state.value} | ws:{status.workspace_root} | "
            f"profile:{status.active_profile_id or 'none'} | {auth} | {plan} | {think} | turns:{turns}{compat}"
        )


class Composer(TextArea):
    """Multi-line composer: Enter submits; Shift/Ctrl+Enter inserts a newline.

    Ctrl+Up/Ctrl+Down walk the per-app-lifetime input history. The in-progress
    draft is remembered when leaving it, so Ctrl+Down restores it before
    cycling past the end.
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "New line", priority=True),
        Binding("ctrl+enter", "newline", "New line", priority=True),
        Binding("ctrl+up", "history_prev", "Previous input", priority=True),
        Binding("ctrl+down", "history_next", "Next input", priority=True),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index = 0
        self._draft = ""

    def push_history(self, text: str) -> None:
        if self._history and self._history[-1] == text:
            return
        self._history.append(text)
        self._history_index = len(self._history)
        self._draft = ""

    def action_history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index == len(self._history):
            self._draft = self.text  # remember the in-progress input
        self._history_index = max(0, self._history_index - 1)
        self.text = self._history[self._history_index] if self._history_index < len(self._history) else self._draft

    def action_history_next(self) -> None:
        if not self._history:
            return
        self._history_index = min(len(self._history) + 1, self._history_index + 1)
        if self._history_index < len(self._history):
            self.text = self._history[self._history_index]
        elif self._history_index == len(self._history):
            self.text = self._draft  # back at the editing slot: restore the draft
        else:
            self.text = ""

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    class Submitted(Message):
        """Carries the submitted composer text to the app handler."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text
