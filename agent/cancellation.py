"""Cooperative cancellation support for Kairo 0.2.6-beta.

Provides a simple :class:`CancellationToken` used to stop the current LLM
generation or tool loop when the user presses ``Esc`` in the Textual UI.

Cancellation is strictly cooperative: long-running OS tool calls are never
force-killed. The streaming loop checks the token before reading the next
chunk; the tool loop checks after each tool finishes and declines to start the
next LLM round when cancelled.
"""
from __future__ import annotations


class CancellationToken:
    """A one-shot cooperative cancel flag."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation. Idempotent."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` has been called."""
        return self._cancelled

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self._cancelled
