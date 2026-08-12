"""Shared fakes for V2 tests: programmable kernel event sources."""

from __future__ import annotations

import asyncio

from kairo_kernel.runtime import SubscriberOverflow


class FakeKernel:
    """Public-surface kernel fake exposing an events facade."""

    def __init__(self) -> None:
        self.events = FakeEvents()


class FakeEvents:
    """Public-surface events fake: replay buffer plus live queue."""

    def __init__(self) -> None:
        self._history: list[object] = []
        self._live: asyncio.Queue[object] = asyncio.Queue()
        self.subscribe_calls: list[int] = []

    async def emit(self, event: object) -> None:
        self._history.append(event)
        await self._live.put(event)

    async def subscribe(self, after_sequence: int = 0, queue_size: int | None = None) -> FakeSubscription:
        self.subscribe_calls.append(after_sequence)
        replay = tuple(event for event in self._history if int(event.sequence) > after_sequence)  # type: ignore[attr-defined]
        return FakeSubscription(self, replay, queue_size or 256)

    async def _next_live(self) -> object:
        return await self._live.get()


class FakeSubscription:
    """Replay-then-live subscription; can fail once to exercise overflow."""

    def __init__(
        self,
        owner: FakeEvents,
        replay: tuple[object, ...],
        queue_size: int,
        *,
        overflow_after: int | None = None,
    ) -> None:
        self._owner = owner
        self._replay = list(replay)
        self._queue_size = queue_size
        self._overflow_after = overflow_after
        self._delivered = 0

    async def receive(self) -> object:
        if self._overflow_after is not None and self._delivered >= self._overflow_after:
            self._overflow_after = None
            raise SubscriberOverflow(self._delivered, 5)
        if self._replay:
            self._delivered += 1
            return self._replay.pop(0)
        event = await self._owner._next_live()
        self._delivered += 1
        return event
