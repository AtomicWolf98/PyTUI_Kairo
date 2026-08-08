from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from kairo_kernel.contracts.enums import (
    ErrorCode,
    EventType,
    InteractionAction,
    InteractionKind,
    LifecycleState,
)
from kairo_kernel.contracts.events import NoticeEvent
from kairo_kernel.contracts.identifiers import InteractionId, KernelId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest, InteractionResponse
from kairo_kernel.runtime import (
    AsyncLifecycle,
    CancellationToken,
    EventBus,
    InteractionBroker,
    InteractionBrokerError,
    SessionTurnSupervisor,
    SubscriberOverflow,
    WorkspaceLeaseManager,
)


def interaction(
    identifier: str,
    *,
    kind: InteractionKind = InteractionKind.TOOL_APPROVAL,
    timeout: float | None = 1.0,
    safe_default: InteractionAction = InteractionAction.REJECT,
) -> InteractionRequest:
    expires_at = None if timeout is None else datetime.now(timezone.utc) + timedelta(seconds=timeout)
    choices = (
        InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve"),
        InteractionChoice(InteractionAction.REJECT, "Reject"),
        InteractionChoice(InteractionAction.STOP, "Stop"),
        InteractionChoice(InteractionAction.SUBMIT_TEXT, "Submit"),
    )
    return InteractionRequest(
        InteractionId(identifier),
        TurnId("turn-1"),
        SessionId("session-1"),
        kind,
        "Continue?",
        choices,
        expires_at,
        safe_default,
    )


async def wait_pending(broker: InteractionBroker, count: int = 1) -> None:
    for _ in range(100):
        if len(await broker.pending()) == count:
            return
        await asyncio.sleep(0)
    raise AssertionError("Interaction was not registered.")


def test_cancellation_token_is_idempotent_and_awaitable() -> None:
    async def exercise() -> None:
        token = CancellationToken()
        waiter = asyncio.create_task(token.wait())
        assert token.cancel("stop")
        await waiter
        assert token.cancelled
        assert token.reason == "stop"
        assert not token.cancel("again")

    asyncio.run(exercise())


def test_lifecycle_serializes_start_shutdown_and_timeout() -> None:
    calls: list[str] = []

    async def start() -> None:
        calls.append("start")

    async def stop() -> None:
        calls.append("stop")

    async def exercise() -> None:
        lifecycle = AsyncLifecycle(start, stop)
        assert (await lifecycle.start()).value is LifecycleState.RUNNING
        assert (await lifecycle.start()).value is LifecycleState.RUNNING
        assert (await lifecycle.shutdown()).value is LifecycleState.STOPPED
        assert (await lifecycle.shutdown()).value is LifecycleState.STOPPED
        assert calls == ["start", "stop"]

        blocked = AsyncLifecycle(shutdown_hook=lambda: asyncio.sleep(1))
        assert (await blocked.start()).ok
        failed = await blocked.shutdown(0.001)
        assert failed.error is not None and failed.error.code is ErrorCode.SHUTDOWN_TIMEOUT
        assert blocked.state is LifecycleState.DEGRADED

    asyncio.run(exercise())


def test_event_bus_concurrent_emit_has_one_global_sequence() -> None:
    async def exercise() -> None:
        bus = EventBus(KernelId("kernel"), max_buffer=200)
        await asyncio.gather(
            *(bus.emit(EventType.NOTICE, NoticeEvent("info", str(index))) for index in range(100))
        )
        replay = await bus.snapshot()
        assert [event.sequence for event in replay.events] == list(range(1, 101))
        assert len({event.event_id for event in replay.events}) == 100

    asyncio.run(exercise())


def test_event_replay_gap_and_subscribe_race_have_no_duplicates() -> None:
    async def exercise() -> None:
        bus = EventBus(KernelId("kernel"), max_buffer=3)
        for index in range(5):
            await bus.emit(EventType.NOTICE, NoticeEvent("info", str(index)))
        replay = await bus.snapshot(0)
        assert replay.gap
        assert [event.sequence for event in replay.events] == [3, 4, 5]

        subscription = await bus.subscribe(3, queue_size=10)
        emit_task = asyncio.create_task(bus.emit(EventType.NOTICE, NoticeEvent("info", "live")))
        received = [await subscription.receive() for _ in range(3)]
        await emit_task
        assert [event.sequence for event in received] == [4, 5, 6]
        await subscription.close()

    asyncio.run(exercise())


def test_slow_subscriber_does_not_block_and_reports_overflow() -> None:
    async def exercise() -> None:
        bus = EventBus(KernelId("kernel"), subscriber_queue_size=2)
        subscription = await bus.subscribe()
        await asyncio.wait_for(
            asyncio.gather(
                *(bus.emit(EventType.NOTICE, NoticeEvent("info", str(index))) for index in range(10))
            ),
            0.5,
        )
        with pytest.raises(SubscriberOverflow) as captured:
            await subscription.receive()
        assert captured.value.dropped_events == 8
        assert [await subscription.receive(), await subscription.receive()][0].sequence == 9

    asyncio.run(exercise())


def test_interaction_broker_accepts_once_and_rejects_illegal_or_duplicate_responses() -> None:
    async def exercise() -> None:
        broker = InteractionBroker()
        token = CancellationToken()
        request = interaction("one")
        waiter = asyncio.create_task(broker.request(request, token))
        await wait_pending(broker)

        wrong_turn = await broker.respond(
            InteractionResponse(request.interaction_id, TurnId("wrong"), InteractionAction.REJECT)
        )
        assert wrong_turn.error is not None and wrong_turn.error.code is ErrorCode.INVALID_ARGUMENT
        illegal = await broker.respond(
            InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.ENABLE_YOLO)
        )
        assert illegal.error is not None and illegal.error.code is ErrorCode.INVALID_ARGUMENT

        accepted = await broker.respond(
            InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.APPROVE_ONCE)
        )
        assert accepted.ok
        assert (await waiter).action is InteractionAction.APPROVE_ONCE
        duplicate = await broker.respond(
            InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.REJECT)
        )
        assert duplicate.error is not None and duplicate.error.code is ErrorCode.CONFLICT

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("kind", "safe_default"),
    (
        (InteractionKind.TOOL_APPROVAL, InteractionAction.REJECT),
        (InteractionKind.PLAN_APPROVAL, InteractionAction.STOP),
        (InteractionKind.TEXT_INPUT, InteractionAction.STOP),
    ),
)
def test_interaction_timeout_is_fail_closed(kind: InteractionKind, safe_default: InteractionAction) -> None:
    async def exercise() -> None:
        broker = InteractionBroker()
        request = interaction("timeout", kind=kind, timeout=0.001, safe_default=safe_default)
        response = await broker.request(request, CancellationToken())
        assert response.action is safe_default
        late = await broker.respond(response)
        assert late.error is not None and late.error.code is ErrorCode.INTERACTION_EXPIRED

    asyncio.run(exercise())


def test_interaction_cancel_shutdown_and_duplicate_request_fail_closed() -> None:
    async def exercise() -> None:
        broker = InteractionBroker()
        token = CancellationToken()
        request = interaction("cancel", timeout=None)
        waiter = asyncio.create_task(broker.request(request, token))
        await wait_pending(broker)
        duplicate = asyncio.create_task(broker.request(request, CancellationToken()))
        with pytest.raises(InteractionBrokerError) as captured:
            await duplicate
        assert captured.value.error.code is ErrorCode.CONFLICT
        token.cancel()
        assert (await waiter).action is InteractionAction.REJECT

        shutdown_request = interaction("shutdown", timeout=None, safe_default=InteractionAction.STOP)
        shutdown_waiter = asyncio.create_task(broker.request(shutdown_request, CancellationToken()))
        await wait_pending(broker)
        await broker.shutdown()
        assert (await shutdown_waiter).action is InteractionAction.STOP
        after = await broker.respond(
            InteractionResponse(shutdown_request.interaction_id, shutdown_request.turn_id, InteractionAction.STOP)
        )
        assert after.error is not None and after.error.code is ErrorCode.KERNEL_CLOSING

    asyncio.run(exercise())


def test_text_interaction_requires_nonempty_text() -> None:
    async def exercise() -> None:
        broker = InteractionBroker()
        request = interaction("text", kind=InteractionKind.TEXT_INPUT)
        waiter = asyncio.create_task(broker.request(request, CancellationToken()))
        await wait_pending(broker)
        empty = await broker.respond(
            InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.SUBMIT_TEXT, "  ")
        )
        assert empty.error is not None and empty.error.code is ErrorCode.INVALID_ARGUMENT
        assert (
            await broker.respond(
                InteractionResponse(request.interaction_id, request.turn_id, InteractionAction.SUBMIT_TEXT, "change")
            )
        ).ok
        assert (await waiter).text == "change"

    asyncio.run(exercise())


def test_session_turn_supervisor_serializes_per_session_only() -> None:
    async def exercise() -> None:
        supervisor = SessionTurnSupervisor()
        first = await supervisor.start(SessionId("one"), TurnId("turn-1"))
        assert first.ok and first.value is not None
        blocked = await supervisor.start(SessionId("one"), TurnId("turn-2"))
        assert blocked.error is not None and blocked.error.code is ErrorCode.KERNEL_BUSY
        other = await supervisor.start(SessionId("two"), TurnId("turn-2"))
        assert other.ok and other.value is not None
        assert len(await supervisor.active()) == 2
        await first.value.release()
        await other.value.release()
        assert await supervisor.wait_idle(0.1)
        await supervisor.close_admission()
        closed = await supervisor.start(SessionId("one"), TurnId("turn-3"))
        assert closed.error is not None and closed.error.code is ErrorCode.KERNEL_CLOSING

    asyncio.run(exercise())


def test_workspace_readers_share_writer_is_exclusive_and_preferred() -> None:
    async def exercise() -> None:
        manager = WorkspaceLeaseManager("old")
        first_reader = await manager.read()
        writer_task = asyncio.create_task(manager.write())
        await asyncio.sleep(0)
        second_reader_task = asyncio.create_task(manager.read())
        await asyncio.sleep(0)
        assert not writer_task.done() and not second_reader_task.done()

        await first_reader.release()
        writer = await asyncio.wait_for(writer_task, 0.1)
        assert not second_reader_task.done()
        updated = await manager.update(writer, "new")
        assert updated.root == "new" and updated.revision == 1
        await writer.release()
        second_reader = await asyncio.wait_for(second_reader_task, 0.1)
        assert second_reader.snapshot == updated
        await second_reader.release()

    asyncio.run(exercise())
