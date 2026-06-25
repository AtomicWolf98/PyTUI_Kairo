"""Cooperative cancellation support for Kairo 0.2.6-beta.

Provides a simple :class:`CancellationToken` used to stop the current LLM
generation or tool loop when the user presses ``Esc`` in the Textual UI.

Cancellation is strictly cooperative: long-running OS tool calls are never
force-killed. The streaming loop checks the token before reading the next
chunk and registers stream cleanup callbacks where possible; the tool loop
checks after each tool finishes and declines to start the next LLM round when
cancelled.
"""
from __future__ import annotations

from threading import Lock
from typing import Callable, List


class CancellationToken:
    """A one-shot cooperative cancel flag."""

    __slots__ = ("_callbacks", "_cancelled", "_lock")

    def __init__(self) -> None:
        self._cancelled = False
        self._callbacks: List[Callable[[], None]] = []
        self._lock = Lock()

    def cancel(self) -> None:
        """Request cancellation. Idempotent."""
        callbacks: List[Callable[[], None]] = []
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def add_cancel_callback(self, callback: Callable[[], None]) -> None:
        """Run *callback* when the token is cancelled.

        If cancellation already happened, the callback runs immediately. This
        lets streaming clients register response cleanup from worker threads
        while the UI thread can still request a prompt stop with Esc.
        """
        run_now = False
        with self._lock:
            if self._cancelled:
                run_now = True
            else:
                self._callbacks.append(callback)
        if run_now:
            try:
                callback()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` has been called."""
        with self._lock:
            return self._cancelled

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self._cancelled
