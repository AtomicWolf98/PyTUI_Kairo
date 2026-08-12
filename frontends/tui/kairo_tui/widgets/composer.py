"""The always-inputable composer; provider state never disables it."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class Composer(TextArea):
    """Multi-line input that never disables before shutdown.

    Enter posts a submit intent without clearing the draft; the controller
    clears it only after the kernel accepts the turn. Shift/Ctrl/Alt+Enter
    insert a newline. The placeholder is a hint only, never a field label.
    """

    class Submitted(Message):
        """Enter was pressed; ``text`` is the exact current composer content."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("shift+enter", "ctrl+enter", "alt+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)
