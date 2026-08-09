"""EventPump: delivery, resubscribe, gap/overflow recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import EventType, LifecycleState, MessageRole, TurnStatus
from kairo_kernel.contracts.events import KernelEvent, LifecycleEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId
from kairo_kernel.contracts.providers import ProviderStreamEvent, ProviderStreamKind
from kairo_kernel.contracts.turns import TurnRequest

from kairo_tui.event_pump import EventPump, ReplayGap, is_subscriber_overflow  # noqa: F401
from kairo_tui.store import AppState, AppStore, EventAction  # noqa: F401
from tests.support.fakes import FakeProvider


class StubEventSource:
    """Stands in for kernel.events; scripted gap/overflow behavior."""

    def __init__(self, *, gap: bool = False, overflow_once: bool = False) -> None:
        self.gap = gap
        self.overflow_once = overflow_once
        self.subscribed_after: list[int] = []
        self.closed = False
        self.pending: list[KernelEvent] = []
        self._wake = asyncio.Event()
        # Stands in for kernel.events; the pump reaches it as kernel.events.
        self.events = self

    async def snapshot(self, after_sequence: int = 0, limit: int = 1000):
        from kairo_kernel.contracts.events import EventReplay

        gap = self.gap
        self.gap = False  # report the gap once; later snapshots are clean
        return EventReplay(tuple(self.pending), 1, max(1, len(self.pending)), gap)

    async def subscribe(self, after_sequence: int = 0, queue_size: int | None = None):
        self.subscribed_after.append(after_sequence)

        class Sub:
            def __init__(self, owner: StubEventSource) -> None:
                self.owner = owner

            async def receive(self):
                if self.owner.closed:
                    raise RuntimeError("Event subscription is closed.")
                if self.owner.overflow_once:
                    self.owner.overflow_once = False
                    raise RuntimeError("Subscriber lost 3 event(s) after sequence 5.")
                if self.owner.pending:
                    return self.owner.pending.pop(0)
                await self.owner._wake.wait()
                if self.owner.closed:
                    raise RuntimeError("Event subscription is closed.")

            async def close(self) -> None:
                self.owner.closed = True
                self.owner._wake.set()

        return Sub(self)


def _event(sequence: int) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.LIFECYCLE, LifecycleEvent(LifecycleState.RUNNING),
    )


def _content(text: str) -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.CONTENT, content=(TextBlock(text),))


def _completed() -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED)


def test_is_subscriber_overflow_detects_by_name() -> None:
    error = RuntimeError("Subscriber lost 1 event(s) after sequence 3.")
    assert is_subscriber_overflow(error) is True
    assert is_subscriber_overflow(RuntimeError("Event subscription is closed.")) is False


def test_pump_dispatches_events_and_resubscribes_from_last_sequence() -> None:
    source = StubEventSource()
    source.pending = [_event(1), _event(2)]
    store = AppStore(AppState())
    pump = EventPump(source, store)
    dispatched: list[int] = []
    store.subscribe(lambda state: dispatched.append(state.last_event_sequence))

    async def exercise() -> None:
        task = asyncio.create_task(pump.run())
        await asyncio.sleep(0.05)
        source.pending.append(_event(3))
        await asyncio.sleep(0.05)
        await pump.close()
        await asyncio.wait_for(task, 1)

    asyncio.run(exercise())
    assert dispatched[-1] == 3
    assert source.subscribed_after == [0]  # single continuous subscription


def test_pump_recovers_on_subscriber_overflow() -> None:
    source = StubEventSource(overflow_once=True)
    store = AppStore(AppState())
    pump = EventPump(source, store)
    recovered: list[int] = []

    async def exercise() -> None:
        original = pump._recover

        async def fake_recover(sequence: int | None = None) -> None:
            recovered.append(store.state.last_event_sequence)
            await original(sequence)

        pump._recover = fake_recover  # type: ignore[method-assign]
        task = asyncio.create_task(pump.run())
        await asyncio.sleep(0.1)
        await pump.close()
        await asyncio.wait_for(task, 1)

    asyncio.run(exercise())
    assert recovered == [0]
    assert source.subscribed_after == [0, 0]  # resubscribed after recovery


def test_replay_gap_recovers_then_resubscribes_from_newest_sequence() -> None:
    source = StubEventSource(gap=True)  # gap reported only on the first snapshot
    source.pending = [_event(1), _event(2)]
    store = AppStore(AppState())
    pump = EventPump(source, store)
    recovered: list[int | None] = []

    async def exercise() -> None:
        original = pump._recover

        async def fake_recover(sequence: int | None = None) -> None:
            recovered.append(sequence)
            await original(sequence)

        pump._recover = fake_recover  # type: ignore[method-assign]
        task = asyncio.create_task(pump.run())
        await asyncio.sleep(0.1)
        await pump.close()
        await asyncio.wait_for(task, 1)

    asyncio.run(exercise())
    assert recovered == [2]  # gap → recovery re-read, told the newest sequence
    assert store.state.last_event_sequence == 2  # store advanced past the gap
    assert source.subscribed_after == [2]  # resubscribed from newest; a gap cannot recur
    assert [event.sequence for event in store.state.events] == [1, 2]  # loop kept running


def test_pump_recovery_rereads_real_kernel_state(kernel_factory) -> None:
    kernel = kernel_factory()

    async def exercise() -> None:
        await kernel.start()
        store = AppStore(AppState())
        pump = EventPump(kernel, store)
        task = asyncio.create_task(pump.run())
        await asyncio.sleep(0.05)
        created = await kernel.sessions.create("Notes")
        assert created.ok
        await asyncio.sleep(0.1)
        # Incremental path: the SESSION_CHANGED event reached the store's log.
        assert any(event.event_type is EventType.SESSION_CHANGED for event in store.state.events)
        await pump.close()
        await kernel.shutdown()
        await asyncio.wait_for(task, 1)

    asyncio.run(exercise())


def test_pump_recovery_rereads_conversations(kernel_factory) -> None:
    """Recovery re-reads committed conversation history into the store: the
    assistant message is present, user records are skipped, nothing duplicates."""
    provider = FakeProvider((_content("Hello world"), _completed()))
    kernel = kernel_factory(provider=provider)

    async def exercise() -> None:
        await kernel.start()
        store = AppStore(AppState())
        pump = EventPump(kernel, store)
        created = await kernel.sessions.create("Notes")
        assert created.ok and created.value is not None
        session_id = created.value.session_id
        accepted = await kernel.submit(TurnRequest("hi", session_id=session_id))
        assert accepted.ok and accepted.value is not None
        result = await kernel.wait(accepted.value.turn_id)
        assert result.ok and result.value is not None
        assert result.value.status is TurnStatus.SUCCEEDED
        # No events were pumped, so only the recovery path fills the store.
        assert store.state.messages == ()
        await pump._recover()
        assert len(store.state.messages) == 1
        (message,) = store.state.messages
        assert message.role is MessageRole.ASSISTANT  # no USER records from history
        assert message.complete is True
        assert str(message.session_id) == str(session_id)
        joined = "".join(block.text for block in message.content if isinstance(block, TextBlock))
        assert joined == "Hello world"
        # Re-running recovery dedupes by message_id: still exactly one record.
        await pump._recover()
        assert len(store.state.messages) == 1
        await pump.close()
        await kernel.shutdown()

    asyncio.run(exercise())
