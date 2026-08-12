"""Read-only transcript for the active session.

Rendering merges consecutive transcript entries that share a message_id
(streamed deltas), separates thought blocks into a collapsed section, and
renders user/assistant messages as message widgets. A Stop button appears
while a turn is active; Retry appears after a failed or stopped turn.
"""

from __future__ import annotations

from textual import on
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static

from kairo_tui_v2.state import SessionTranscript, TranscriptEntry
from kairo_tui_v2.widgets.message import MessageWidget


def merge_deltas(entries: tuple[TranscriptEntry, ...]) -> tuple[TranscriptEntry, ...]:
    """Merge adjacent entries with the same message_id into one message."""
    merged: list[TranscriptEntry] = []
    for entry in entries:
        if merged and merged[-1].message_id == entry.message_id:
            previous = merged[-1]
            merged[-1] = TranscriptEntry(
                previous.message_id,
                previous.role,
                previous.kind,
                previous.content + entry.content,
                previous.name,
            )
        else:
            merged.append(entry)
    return tuple(merged)


class Transcript(VerticalScroll):
    """Scrollable message history; renders the active session transcript."""

    class StopPressed(Message):
        """The Stop button was pressed."""

    class RetryPressed(Message):
        """The Retry button was pressed."""

    async def render_session(
        self,
        transcript: SessionTranscript | None,
        *,
        stopping: bool = False,
        can_retry: bool = False,
    ) -> None:
        await self.remove_children()
        if transcript is None or not transcript.entries:
            self.mount(Static("Ask Kairo something to begin.", id="transcript-empty"))
        else:
            for entry in merge_deltas(transcript.entries):
                self.mount(MessageWidget(entry))
        if stopping:
            self.mount(Button("Stopping…", id="stop-turn", disabled=True))
        else:
            self.mount(Button("Stop", id="stop-turn"))
        if can_retry:
            self.mount(Button("Retry", id="retry-turn"))

    @on(Button.Pressed, "#stop-turn")
    def _on_stop_pressed(self, message: Button.Pressed) -> None:
        self.post_message(self.StopPressed())

    @on(Button.Pressed, "#retry-turn")
    def _on_retry_pressed(self, message: Button.Pressed) -> None:
        self.post_message(self.RetryPressed())
