from contextlib import contextmanager
from typing import Any, Callable

from textual.message import Message


class AgentEvent(Message):
    def __init__(self, kind: str, payload: Any = None):
        super().__init__()
        self.kind = kind
        self.payload = payload


class EventConsole:
    """Small Console-compatible bridge used by the background Agent worker."""

    def __init__(self, emit: Callable[[str, Any], None]):
        self.emit = emit

    def print(self, *objects, **_kwargs):
        if not objects:
            self.emit("console", "")
        elif len(objects) == 1:
            self.emit("console", objects[0])
        else:
            self.emit("console", " ".join(str(value) for value in objects))

    @contextmanager
    def status(self, message: str, **_kwargs):
        self.emit("status", message)
        try:
            yield self
        finally:
            pass
