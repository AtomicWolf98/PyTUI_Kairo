"""Replayable, non-blocking asyncio event bus."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import cast

from kairo_kernel.contracts.enums import EventType
from kairo_kernel.contracts.events import EventPayload, EventReplay, KernelEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, SessionId, TurnId


class SubscriberOverflow(RuntimeError):
    """Raised once when a subscriber has lost live events."""

    def __init__(self, last_delivered_sequence: int, dropped_events: int) -> None:
        self.last_delivered_sequence = last_delivered_sequence
        self.dropped_events = dropped_events
        super().__init__(f"Subscriber lost {dropped_events} event(s) after sequence {last_delivered_sequence}.")


class EventSubscription:
    def __init__(self, bus: EventBus, replay: tuple[KernelEvent, ...], queue_size: int) -> None:
        self._bus = bus
        self._replay = deque(replay)
        self._queue: asyncio.Queue[KernelEvent | object] = asyncio.Queue(maxsize=max(1, queue_size))
        self._closed = False
        self._dropped = 0
        self._last_delivered = replay[0].sequence - 1 if replay else bus.sequence

    async def receive(self) -> KernelEvent:
        if self._closed:
            raise RuntimeError("Event subscription is closed.")
        if self._replay:
            event = self._replay.popleft()
            self._last_delivered = event.sequence
            return event
        if self._dropped:
            dropped = self._dropped
            self._dropped = 0
            raise SubscriberOverflow(self._last_delivered, dropped)
        queued = await self._queue.get()
        if queued is _CLOSED:
            raise RuntimeError("Event subscription is closed.")
        event = cast(KernelEvent, queued)
        self._last_delivered = event.sequence
        return event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wake_closed()
        await self._bus._unsubscribe(self)

    def _offer(self, event: KernelEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            self._queue.get_nowait()
            self._dropped += 1
        self._queue.put_nowait(event)

    def _wake_closed(self) -> None:
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSED)


_CLOSED = object()


class EventBus:
    """Assign global sequences and atomically bridge replay to live delivery."""

    def __init__(self, kernel_id: KernelId, max_buffer: int = 1000, subscriber_queue_size: int = 256) -> None:
        self.kernel_id = kernel_id
        self.max_buffer = max(1, max_buffer)
        self.subscriber_queue_size = max(1, subscriber_queue_size)
        self._events: deque[KernelEvent] = deque(maxlen=self.max_buffer)
        self._subscribers: set[EventSubscription] = set()
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def sequence(self) -> int:
        return self._sequence

    async def emit(
        self,
        event_type: EventType,
        payload: EventPayload,
        *,
        turn_sequence: int | None = None,
        turn_id: TurnId | None = None,
        session_id: SessionId | None = None,
        workspace_revision: int = 0,
    ) -> KernelEvent:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Event bus is closed.")
            self._sequence += 1
            event = KernelEvent(
                EventId(uuid.uuid4().hex),
                self.kernel_id,
                self._sequence,
                datetime.now(timezone.utc),
                event_type,
                payload,
                turn_sequence=turn_sequence,
                turn_id=turn_id,
                session_id=session_id,
                workspace_revision=workspace_revision,
            )
            self._events.append(event)
            for subscriber in tuple(self._subscribers):
                subscriber._offer(event)
            return event

    async def publish(self, event: KernelEvent) -> None:
        await self.emit(
            event.event_type,
            event.payload,
            turn_sequence=event.turn_sequence,
            turn_id=event.turn_id,
            session_id=event.session_id,
            workspace_revision=event.workspace_revision,
        )

    async def snapshot(self, after_sequence: int = 0, limit: int = 1000) -> EventReplay:
        async with self._lock:
            return self._snapshot_locked(after_sequence, limit)

    def _snapshot_locked(self, after_sequence: int, limit: int) -> EventReplay:
        oldest = self._events[0].sequence if self._events else self._sequence + 1
        newest = self._events[-1].sequence if self._events else self._sequence
        gap = after_sequence < oldest - 1
        events = tuple(event for event in self._events if event.sequence > after_sequence)[: max(0, limit)]
        return EventReplay(events, oldest, newest, gap)

    async def subscribe(self, after_sequence: int = 0, queue_size: int | None = None) -> EventSubscription:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Event bus is closed.")
            replay = self._snapshot_locked(after_sequence, self.max_buffer)
            subscription = EventSubscription(self, replay.events, queue_size or self.subscriber_queue_size)
            self._subscribers.add(subscription)
            return subscription

    async def _unsubscribe(self, subscription: EventSubscription) -> None:
        async with self._lock:
            self._subscribers.discard(subscription)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            subscriptions = tuple(self._subscribers)
            self._subscribers.clear()
            for subscription in subscriptions:
                subscription._closed = True
                subscription._wake_closed()
