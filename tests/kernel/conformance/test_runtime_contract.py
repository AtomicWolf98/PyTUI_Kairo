from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kairo_kernel.contracts.enums import EventType, InteractionAction, InteractionKind, LifecycleState
from kairo_kernel.contracts.events import NoticeEvent
from kairo_kernel.contracts.identifiers import InteractionId, KernelId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionChoice, InteractionRequest
from kairo_kernel.runtime import CancellationToken, EventBus, InteractionBroker, SubscriberOverflow


@pytest.mark.asyncio
async def test_replay_gap_and_subscriber_overflow_are_explicit() -> None:
    bus = EventBus(KernelId("conformance"), max_buffer=3, subscriber_queue_size=2)
    subscription = await bus.subscribe()
    for index in range(8):
        await bus.emit(EventType.NOTICE, NoticeEvent("info", str(index)))
    replay = await bus.snapshot(0)
    assert replay.gap
    assert tuple(event.sequence for event in replay.events) == (6, 7, 8)
    with pytest.raises(SubscriberOverflow) as captured:
        await subscription.receive()
    assert captured.value.dropped_events == 6
    await subscription.close()
    await bus.close()


@pytest.mark.asyncio
async def test_approval_timeout_is_fail_closed_even_with_unsafe_default() -> None:
    broker = InteractionBroker()
    with pytest.raises(ValueError, match="fail closed"):
        InteractionRequest(
            InteractionId("unsafe"),
            TurnId("turn"),
            SessionId("session"),
            InteractionKind.TOOL_APPROVAL,
            "Approve?",
            (
                InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve"),
                InteractionChoice(InteractionAction.REJECT, "Reject"),
            ),
            None,
            InteractionAction.APPROVE_ONCE,
        )
    request = InteractionRequest(
        InteractionId("approval"),
        TurnId("turn"),
        SessionId("session"),
        InteractionKind.TOOL_APPROVAL,
        "Approve?",
        (
            InteractionChoice(InteractionAction.APPROVE_ONCE, "Approve"),
            InteractionChoice(InteractionAction.REJECT, "Reject"),
        ),
        datetime.now(timezone.utc) + timedelta(milliseconds=1),
        InteractionAction.REJECT,
    )
    response = await broker.request(request, CancellationToken())
    assert response.action is InteractionAction.REJECT
    await broker.shutdown()


@pytest.mark.asyncio
async def test_kernel_lifecycle_is_idempotent(tmp_path: Path) -> None:
    from kairo_kernel.testing import ConformanceHarness

    kernel = ConformanceHarness.create(tmp_path).kernel
    first_start = await kernel.start()
    second_start = await kernel.start()
    assert first_start.value is LifecycleState.RUNNING
    assert second_start.value is LifecycleState.RUNNING
    first = await kernel.shutdown()
    second = await kernel.shutdown()
    assert first.value is not None and second.value == first.value
    assert first.value.resources_closed == ("interactions", "mcp", "workspace", "database")
