from __future__ import annotations

import asyncio

from kairo_kernel.contracts.content import Message, ReasoningBlock, TextBlock, ToolCallBlock, ToolResultBlock
from kairo_kernel.contracts.enums import (
    ErrorCode,
    EventType,
    InteractionAction,
    MessageKind,
    MessageRole,
    ProviderFailureKind,
    ProviderStreamKind,
    ToolExecutionStatus,
    TurnStatus,
)
from kairo_kernel.contracts.events import TurnEvent
from kairo_kernel.contracts.identifiers import KernelId, MessageId, SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderFailure, ProviderProfile, ProviderStreamEvent, ProviderUsage
from kairo_kernel.contracts.turns import TurnRequest, TurnResult
from kairo_kernel.engine import EngineOptions, TurnEngine
from kairo_kernel.runtime import EventBus, InteractionBroker
from tests.kernel.engine.fakes import (
    PROFILE,
    FakeAuthorization,
    FakeInteractions,
    FakeProvider,
    FakeSessions,
    FakeTool,
    FakeTools,
    session,
)


def _completed(text: str = "done") -> tuple[ProviderStreamEvent, ...]:
    return (
        ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock(text),)),
        ProviderStreamEvent(ProviderStreamKind.COMPLETED),
    )


def _engine(
    provider: FakeProvider,
    sessions: FakeSessions,
    *,
    tools: FakeTools | None = None,
    interactions: FakeInteractions | InteractionBroker | None = None,
    authorization: FakeAuthorization | None = None,
    options: EngineOptions | None = None,
) -> tuple[TurnEngine, EventBus]:
    bus = EventBus(KernelId("kernel"), max_buffer=1000)
    engine = TurnEngine(
        provider=provider,
        tools=tools or FakeTools(),
        sessions=sessions,
        events=bus,
        interactions=interactions or FakeInteractions(),
        authorization=authorization or FakeAuthorization(),
        options=options or EngineOptions(default_session_id=SessionId("session-1")),
    )
    return engine, bus


async def _run(
    engine: TurnEngine,
    text: str = "hello",
    session_id: str = "session-1",
) -> tuple[TurnId, TurnResult]:
    accepted = await engine.submit(TurnRequest(text, SessionId(session_id)))
    assert accepted.value is not None
    result = await engine.wait(accepted.value.turn_id, 1)
    assert result.value is not None
    return accepted.value.turn_id, result.value


def test_text_reasoning_usage_history_and_one_terminal_event() -> None:
    async def exercise() -> None:
        provider = FakeProvider(
            (
                ProviderStreamEvent(ProviderStreamKind.REASONING, (TextBlock("thought"),)),
                ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock("answer"),)),
                ProviderStreamEvent(ProviderStreamKind.USAGE, usage=ProviderUsage(10, 2)),
                ProviderStreamEvent(ProviderStreamKind.COMPLETED),
            )
        )
        sessions = FakeSessions(session())
        engine, bus = _engine(provider, sessions)
        _, result = await _run(engine)
        assert result.status is TurnStatus.SUCCEEDED
        assistant = result.messages[-1]
        assert any(isinstance(block, ReasoningBlock) and block.text == "thought" for block in assistant.content)
        assert any(isinstance(block, TextBlock) and block.text == "answer" for block in assistant.content)
        assert sessions.saves and sessions.saves[-1][0].messages[-1] == assistant
        replay = await bus.snapshot()
        terminals = [
            event
            for event in replay.events
            if event.event_type is EventType.TURN
            and isinstance(event.payload, TurnEvent)
            and event.payload.status in (TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED)
        ]
        assert len(terminals) == 1
        assert [event.turn_sequence for event in replay.events] == list(range(1, len(replay.events) + 1))

    asyncio.run(exercise())


def test_multi_tool_loop_and_output_events() -> None:
    async def exercise() -> None:
        call_one = ToolCallBlock(ToolCallId("call-1"), "one", JsonObject())
        call_two = ToolCallBlock(ToolCallId("call-2"), "two", JsonObject())
        provider = FakeProvider(
            (
                ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call_one),
                ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call_two),
                ProviderStreamEvent(ProviderStreamKind.COMPLETED),
            ),
            _completed("final"),
        )
        one, two = FakeTool("one"), FakeTool("two")
        engine, bus = _engine(provider, FakeSessions(session()), tools=FakeTools(one, two))
        _, result = await _run(engine)
        assert result.status is TurnStatus.SUCCEEDED
        assert len(one.calls) == len(two.calls) == 1
        tool_blocks = [
            message.content[0]
            for message in result.messages
            if message.content and isinstance(message.content[0], ToolResultBlock)
        ]
        assert len(tool_blocks) == 2
        assert all(block.status is ToolExecutionStatus.SUCCEEDED for block in tool_blocks)
        assert any(event.event_type is EventType.TOOL for event in (await bus.snapshot()).events)

    asyncio.run(exercise())


def test_tool_rejection_is_fail_closed_and_turn_continues() -> None:
    async def exercise() -> None:
        call = ToolCallBlock(ToolCallId("call"), "one", JsonObject())
        provider = FakeProvider(
            (
                ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call),
                ProviderStreamEvent(ProviderStreamKind.COMPLETED),
            ),
            _completed(),
        )
        tool = FakeTool("one")
        interactions = FakeInteractions((InteractionAction.REJECT, ""))
        engine, _ = _engine(
            provider,
            FakeSessions(session()),
            tools=FakeTools(tool),
            interactions=interactions,
            authorization=FakeAuthorization(False),
        )
        _, result = await _run(engine)
        assert result.status is TurnStatus.SUCCEEDED
        assert not tool.calls
        rejected = next(
            block
            for message in result.messages
            for block in message.content
            if isinstance(block, ToolResultBlock)
        )
        assert rejected.status is ToolExecutionStatus.REJECTED

    asyncio.run(exercise())


def test_tool_approval_timeout_and_invalid_response_fail_closed() -> None:
    async def exercise() -> None:
        call = ToolCallBlock(ToolCallId("call"), "one", JsonObject())
        tool_script = (
            ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call),
            ProviderStreamEvent(ProviderStreamKind.COMPLETED),
        )
        timed_tool = FakeTool("one")
        timeout_engine, _ = _engine(
            FakeProvider(tool_script, _completed()),
            FakeSessions(session()),
            tools=FakeTools(timed_tool),
            interactions=InteractionBroker(),
            authorization=FakeAuthorization(False),
            options=EngineOptions(default_session_id=SessionId("session-1"), interaction_timeout_seconds=0.001),
        )
        _, timed_out = await _run(timeout_engine)
        assert timed_out.status is TurnStatus.SUCCEEDED
        assert not timed_tool.calls

        invalid_tool = FakeTool("one")
        invalid_engine, _ = _engine(
            FakeProvider(tool_script, _completed()),
            FakeSessions(session()),
            tools=FakeTools(invalid_tool),
            interactions=FakeInteractions((InteractionAction.ENABLE_YOLO, "")),
            authorization=FakeAuthorization(False),
        )
        _, invalid = await _run(invalid_engine)
        assert invalid.status is TurnStatus.SUCCEEDED
        assert not invalid_tool.calls

    asyncio.run(exercise())


def test_plan_edit_and_cancel() -> None:
    async def exercise() -> None:
        options = EngineOptions(default_session_id=SessionId("session-1"), plan_mode=True)
        provider = FakeProvider(_completed("plan"), _completed("answer"))
        interactions = FakeInteractions((InteractionAction.SUBMIT_TEXT, "add tests"))
        engine, _ = _engine(provider, FakeSessions(session()), interactions=interactions, options=options)
        _, edited = await _run(engine)
        assert edited.status is TurnStatus.SUCCEEDED
        chat_request = provider.requests[-1]
        assert "[User Plan Modification]: add tests" in chat_request.messages[-1].to_json()

        cancelled_provider = FakeProvider(_completed("plan"))
        cancelled_sessions = FakeSessions(session())
        cancelled_engine, _ = _engine(
            cancelled_provider,
            cancelled_sessions,
            interactions=FakeInteractions((InteractionAction.STOP, "")),
            options=options,
        )
        _, cancelled = await _run(cancelled_engine)
        assert cancelled.status is TurnStatus.CANCELLED
        assert not cancelled_sessions.saves

    asyncio.run(exercise())


def test_stop_saves_partial_and_next_turn_is_clean() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.block = True
        sessions = FakeSessions(session())
        engine, _ = _engine(provider, sessions)
        accepted = await engine.submit(TurnRequest("first", SessionId("session-1")))
        assert accepted.value is not None
        await asyncio.sleep(0)
        assert (await engine.cancel(accepted.value.turn_id)).ok
        stopped = await engine.wait(accepted.value.turn_id, 1)
        assert stopped.value is not None and stopped.value.status is TurnStatus.CANCELLED
        assert "[stopped]" in stopped.value.messages[-1].to_json()

        provider.block = False
        provider.scripts.append(_completed("next"))
        _, next_result = await _run(engine, "second")
        assert next_result.status is TurnStatus.SUCCEEDED
        assert "next" in next_result.messages[-1].to_json()

    asyncio.run(exercise())


def test_invalid_provider_stream_and_persistence_failure_are_terminal() -> None:
    async def exercise() -> None:
        invalid_engine, _ = _engine(
            FakeProvider((ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock("unterminated"),)),)),
            FakeSessions(session()),
        )
        _, invalid = await _run(invalid_engine)
        assert invalid.status is TurnStatus.FAILED
        assert "without terminal" in invalid.error_message

        sessions = FakeSessions(session())
        sessions.fail_save = True
        persistence_engine, _ = _engine(FakeProvider(_completed()), sessions)
        _, persistence = await _run(persistence_engine)
        assert persistence.status is TurnStatus.FAILED
        assert persistence.error_message == "save failed"

    asyncio.run(exercise())


def test_context_failure_retries_once_and_tool_exception_is_structured() -> None:
    async def exercise() -> None:
        context_failure = ProviderStreamEvent(
            ProviderStreamKind.FAILED,
            failure=ProviderFailure(ProviderFailureKind.CONTEXT, "too long", False),
        )
        provider = FakeProvider((context_failure,), _completed("retried"))
        engine, _ = _engine(provider, FakeSessions(session()))
        _, retried = await _run(engine)
        assert retried.status is TurnStatus.SUCCEEDED
        assert len(provider.requests) == 2

        call = ToolCallBlock(ToolCallId("call"), "broken", JsonObject())
        tool_provider = FakeProvider(
            (
                ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call),
                ProviderStreamEvent(ProviderStreamKind.COMPLETED),
            ),
            _completed(),
        )
        tool_engine, _ = _engine(
            tool_provider,
            FakeSessions(session()),
            tools=FakeTools(FakeTool("broken", raises=True)),
        )
        _, tool_result = await _run(tool_engine)
        assert tool_result.status is TurnStatus.SUCCEEDED
        assert "tool exploded" in tool_result.messages[-2].to_json()

    asyncio.run(exercise())


def test_proactive_context_compaction_preserves_recent_turns() -> None:
    async def exercise() -> None:
        base = session()
        messages = list(base.messages)
        for index in range(6):
            messages.extend(
                (
                    Message(
                        MessageId(f"user-{index}"),
                        MessageRole.USER,
                        MessageKind.CHAT,
                        (TextBlock(f"old user {index} " + "x" * 180),),
                    ),
                    Message(
                        MessageId(f"assistant-{index}"),
                        MessageRole.ASSISTANT,
                        MessageKind.CHAT,
                        (TextBlock(f"old answer {index} " + "y" * 180),),
                    ),
                )
            )
        crowded = type(base)(base.session_id, base.name, tuple(messages), base.created_at, base.updated_at)
        profile = ProviderProfile(
            PROFILE.profile_id,
            PROFILE.label,
            PROFILE.provider,
            PROFILE.model,
            PROFILE.base_url,
            1400,
            200,
            0.2,
        )
        provider = FakeProvider(_completed("summary"), _completed("answer"), profile=profile)
        sessions = FakeSessions(crowded)
        engine, _ = _engine(provider, sessions)
        _, result = await _run(engine)
        assert result.status is TurnStatus.SUCCEEDED
        saved = sessions.saves[-1][0]
        assert saved.compression_count == 1
        assert any(message.kind is MessageKind.SUMMARY for message in saved.messages)
        assert len(provider.requests) == 2

    asyncio.run(exercise())


def test_run_snapshot_keeps_captured_session_during_external_replacement() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.block = True
        original = session()
        sessions = FakeSessions(original)
        engine, _ = _engine(provider, sessions)
        accepted = await engine.submit(TurnRequest("bound", original.session_id))
        assert accepted.value is not None
        sessions.records[original.session_id] = type(original)(
            original.session_id,
            "externally replaced",
            original.messages,
            original.created_at,
            original.updated_at,
        )
        await asyncio.sleep(0)
        await engine.cancel(accepted.value.turn_id)
        result = await engine.wait(accepted.value.turn_id, 1)
        assert result.value is not None and result.value.status is TurnStatus.CANCELLED
        assert sessions.saves[-1][0].name == original.name

    asyncio.run(exercise())


def test_same_session_concurrency_rejected_other_session_allowed() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.block = True
        sessions = FakeSessions(session(), session("session-2"))
        engine, _ = _engine(provider, sessions)
        first = await engine.submit(TurnRequest("one", SessionId("session-1")))
        assert first.ok and first.value is not None
        blocked = await engine.submit(TurnRequest("two", SessionId("session-1")))
        assert blocked.error is not None and blocked.error.code is ErrorCode.KERNEL_BUSY
        other = await engine.submit(TurnRequest("other", SessionId("session-2")))
        assert other.ok and other.value is not None
        await engine.cancel(first.value.turn_id)
        await engine.cancel(other.value.turn_id)
        assert (await engine.wait(first.value.turn_id, 1)).value is not None
        assert (await engine.wait(other.value.turn_id, 1)).value is not None

    asyncio.run(exercise())
