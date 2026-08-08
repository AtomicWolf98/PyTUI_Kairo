"""Asyncio-native cooperative cancellation."""

from __future__ import annotations

import asyncio


class CancellationToken:
    """One-shot cancellation signal safe for tasks on one event loop."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "") -> bool:
        if self._event.is_set():
            return False
        self._reason = reason
        self._event.set()
        return True

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(self._reason)


class CancellationSource:
    """Owner wrapper that exposes a read-only token to collaborators."""

    def __init__(self) -> None:
        self.token = CancellationToken()

    def cancel(self, reason: str = "") -> bool:
        return self.token.cancel(reason)

