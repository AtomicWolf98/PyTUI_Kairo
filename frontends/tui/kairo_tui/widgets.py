"""Shared TUI widgets.

These widgets intentionally contain presentation only.  They receive the
immutable AppState snapshot and never call the kernel directly.
"""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static, TextArea


class TopBar(Static):
    """Compact session/workspace identity line.

    Detailed preferences belong in the status line and command palette.  This
    prevents a full path and a dozen flags from wrapping above the conversation
    on ordinary terminals.
    """

    def render_status(self, state) -> None:
        status = state.kernel_status
        if status is None:
            self.update("KAIRO   starting")
            return
        session = next(
            (item.name for item in state.sessions if str(item.session_id) == state.active_session_id),
            "New session",
        )
        root = (status.workspace_root or "workspace").replace("\\", "/").rstrip("/")
        workspace = root.rsplit("/", 1)[-1] or "workspace"
        health = "safe-mode" if state.safe_mode else status.state.value.lower()
        if state.compat_mode:
            health = "compat mode"
        thinking = "think" if status.thinking_mode else "think-off"
        self.update(f"Kairo  {session}   {workspace}  ·  {health} · {thinking}")


class StatusLine(Static):
    """OpenCode-style prompt context and keyboard hints."""

    def render_state(self, state) -> None:
        status = state.kernel_status
        if status is None:
            self.update("starting · ctrl+p commands")
            return
        profile = status.active_profile_id or "no-profile"
        agent = "PLAN" if status.plan_mode else "BUILD"
        think = "think" if status.thinking_mode else "fast"
        mode = status.authorization_mode.value
        active = f"{len(state.active_turns)} active" if state.active_turns else "idle"
        self.update(
            f"{agent} · {profile} · {mode} · {think} · {active}   "
            "ctrl+p commands · ctrl+x b sidebar · esc stop",
        )


class Composer(TextArea):
    """Multi-line composer with history and explicit submit/newline actions."""

    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("shift+enter", "newline", "New line", priority=True),
        Binding("ctrl+enter", "newline", "New line", priority=True),
        Binding("alt+enter", "newline", "New line", priority=True),
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
            self._draft = self.text
        self._history_index = max(0, self._history_index - 1)
        self.text = self._history[self._history_index] if self._history_index < len(self._history) else self._draft

    def action_history_next(self) -> None:
        if not self._history:
            return
        self._history_index = min(len(self._history) + 1, self._history_index + 1)
        if self._history_index < len(self._history):
            self.text = self._history[self._history_index]
        elif self._history_index == len(self._history):
            self.text = self._draft
        else:
            self.text = ""

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    class Submitted(Message):
        """Carries the composer text to the app handler."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text
