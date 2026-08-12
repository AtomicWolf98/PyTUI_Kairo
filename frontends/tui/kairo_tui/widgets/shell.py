"""The single chat-first workbench shell."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from kairo_tui.widgets.composer import Composer
from kairo_tui.widgets.status import StatusLine
from kairo_tui.widgets.transcript import Transcript


class TopBar(Static):
    """Fixed header: app title plus session/workspace/model labels."""


class Shell(Container):
    """Fixed compose order: TopBar, Transcript, Composer, StatusLine."""

    def compose(self) -> ComposeResult:
        yield TopBar("Kairo — Chat", id="topbar")
        yield Transcript(id="transcript")
        yield Composer(
            id="composer",
            placeholder="Ask Kairo… (Enter submits · Shift+Enter newline)",
        )
        yield StatusLine("Not connected · Ctrl+L composer · Esc stop", id="status-line")
