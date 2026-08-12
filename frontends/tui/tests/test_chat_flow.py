"""C1 acceptance: full chat workflow against a streaming fake kernel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from kairo_kernel.contracts.content import ReasoningBlock, TextBlock
from kairo_kernel.contracts.enums import (
    EventType,
    MessageKind,
    MessageRole,
    TurnPhase,
    TurnStatus,
)
from kairo_kernel.contracts.events import KernelEvent, MessageEvent, ToolEvent, TurnEvent, UsageEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, MessageId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.lifecycle import ContextStats
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SessionSummary
from kairo_kernel.contracts.tools import ToolInvocation, ToolResult
from kairo_kernel.errors import KernelError, KernelResult
from support.fakes import FakeKernel

from kairo_tui.app import KairoTuiApp
from kairo_tui.widgets.composer import Composer

SESSION = SessionId("session-1")
TURN = TurnId("turn-1")
PROFILE = ProviderProfile(
    ProfileId("openai:gpt"),
    "OpenAI",
    "openai_responses",
    "gpt-4o",
    "https://api.openai.com/v1",
    128_000,
    16_384,
    0.7,
)


def _turn_event(sequence: int, status: TurnStatus, phase: TurnPhase | None) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"),
        KernelId("k"),
        sequence,
        datetime.now(timezone.utc),
        EventType.TURN,
        TurnEvent(status, phase),
        turn_id=TURN,
        session_id=SESSION,
    )


def _message_event(sequence: int, message_id: MessageId, action: str, blocks: tuple) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"),
        KernelId("k"),
        sequence,
        datetime.now(timezone.utc),
        EventType.MESSAGE,
        MessageEvent(message_id, action, blocks),
        turn_id=TURN,
        session_id=SESSION,
    )


def _tool_event(sequence: int, action: str, invocation: ToolInvocation, result: ToolResult | None = None) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"),
        KernelId("k"),
        sequence,
        datetime.now(timezone.utc),
        EventType.TOOL,
        ToolEvent(action, invocation=invocation, result=result),
        turn_id=TURN,
        session_id=SESSION,
    )


class StreamingFakeKernel(FakeKernel):
    """Submits turns and streams a canned event sequence."""

    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[str] = []
        self.cancelled: list[TurnId] = []
        self._sequence = 0

        class _Providers:
            def __init__(self, owner: StreamingFakeKernel) -> None:
                self._owner = owner

            async def resolve(self, profile_id: object = None, role: str = "") -> KernelResult[ProviderProfile]:
                return KernelResult.success(PROFILE)

            async def snapshot(self):
                from kairo_kernel.services.providers import ProviderCatalogSnapshot

                return ProviderCatalogSnapshot(0)

        class _Sessions:
            def __init__(self, owner: StreamingFakeKernel) -> None:
                self._owner = owner

            async def list(self) -> KernelResult[tuple[SessionSummary, ...]]:
                return KernelResult.success(
                    (
                        SessionSummary(
                            SESSION,
                            "Chat",
                            0,
                            datetime.now(timezone.utc),
                            datetime.now(timezone.utc),
                        ),
                    )
                )

            async def create(self, name: str) -> KernelResult[SessionSummary]:
                return KernelResult.success(
                    SessionSummary(SESSION, name, 0, datetime.now(timezone.utc), datetime.now(timezone.utc))
                )

        class _Conversations:
            def __init__(self, owner: StreamingFakeKernel) -> None:
                self._owner = owner

            async def history(self, session_id: SessionId):
                from kairo_kernel.contracts.content import Message

                return KernelResult.success(
                    (
                        Message(
                            MessageId("user-1"),
                            MessageRole.USER,
                            MessageKind.CHAT,
                            (TextBlock(self._owner.submitted[-1] if self._owner.submitted else ""),),
                        ),
                    )
                )

        self.providers = _Providers(self)
        self.sessions = _Sessions(self)
        self.conversations = _Conversations(self)

    async def submit(self, request) -> KernelResult:
        from kairo_kernel.contracts.turns import TurnAccepted

        self.submitted.append(request.text)
        self._start_stream(request.text)
        return KernelResult.success(TurnAccepted(TURN, SESSION, datetime.now(timezone.utc)))

    async def cancel(self, turn_id: TurnId, reason: str = "") -> KernelResult:
        from kairo_kernel.contracts.turns import CancelReceipt

        self.cancelled.append(turn_id)
        await self.events.emit(_turn_event(self._next(), TurnStatus.CANCELLED, None))
        return KernelResult.success(CancelReceipt(turn_id, True))

    async def active_turns(self) -> tuple:
        return ()

    async def status(self):
        from kairo_kernel.contracts.enums import LifecycleState
        from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus

        return KernelStatus(
            KernelId("k"),
            LifecycleState.RUNNING,
            "0.4.0a2",
            datetime.now(timezone.utc),
            "/workspace",
            0,
            PROFILE.profile_id,
            SESSION,
            TURN,
            __import__("kairo_kernel.contracts.enums", fromlist=["AuthorizationMode"]).AuthorizationMode.MANUAL,
            False,
            False,
            ContextStats(0, 0, 0.0),
        )

    def _start_stream(self, text: str) -> None:
        async def stream() -> None:
            await asyncio.sleep(0.01)
            message_id = MessageId("assistant-1")
            await self.events.emit(_turn_event(self._next(), TurnStatus.RUNNING, TurnPhase.THINKING))
            await self.events.emit(_message_event(self._next(), message_id, "delta", (ReasoningBlock("thinking hard"),)))
            await self.events.emit(_message_event(self._next(), message_id, "delta", (TextBlock("Hello "),)))
            await self.events.emit(_message_event(self._next(), message_id, "delta", (TextBlock("from Kairo"),)))
            await self.events.emit(_message_event(self._next(), message_id, "completed", ()))
            await self.events.emit(_turn_event(self._next(), TurnStatus.SUCCEEDED, None))
            await self.events.emit(
                KernelEvent(
                    EventId(f"e{self._next()}"),
                    KernelId("k"),
                    self._sequence,
                    datetime.now(timezone.utc),
                    EventType.USAGE,
                    UsageEvent(ContextStats(10, 128_000, 0.0)),
                    turn_id=TURN,
                    session_id=SESSION,
                )
            )

        self._stream_task = asyncio.create_task(stream())

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence


async def test_submit_clears_draft_only_after_acceptance() -> None:
    app = KairoTuiApp(kernel=StreamingFakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        # Draft cleared only after the kernel accepted the turn.
        assert app.query_one("#composer", Composer).text == ""
        assert app.state.active_session_id == SESSION


async def test_submit_failure_keeps_draft() -> None:
    kernel = StreamingFakeKernel()

    async def failing_submit(request) -> KernelResult:
        return KernelResult.failure(
            KernelError(
                __import__("kairo_kernel.contracts.enums", fromlist=["ErrorCode"]).ErrorCode.KERNEL_BUSY,
                "Kernel is busy.",
                operation="turn.submit",
            )
        )

    kernel.submit = failing_submit  # type: ignore[method-assign]
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.query_one("#composer", Composer).text == "hi"


async def test_streaming_content_deltas_merge_into_one_message() -> None:
    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        for _ in range(8):
            await pilot.pause()
        transcript = next(item for item in app.state.transcripts if item.session_id == SESSION)
        matching = [
            entry
            for entry in transcript.entries
            if entry.message_id == MessageId("assistant-1")
        ]
        assert len(matching) == 1  # deltas merged into one message
        texts = [
            block.text
            for entry in matching
            for block in entry.content
            if isinstance(block, TextBlock)
        ]
        assert texts == ["Hello ", "from Kairo"]
        assert app.state.last_sequence >= 6


async def test_thought_is_separate_and_collapsed() -> None:
    from kairo_tui.widgets.message import split_thought

    thought, visible = split_thought((ReasoningBlock("secret reasoning"), TextBlock("visible")))
    assert len(thought) == 1
    assert len(visible) == 1
    assert isinstance(visible[0], TextBlock)


async def test_tool_card_lifecycle() -> None:
    from datetime import timedelta

    from kairo_kernel.contracts.enums import OperationScope
    from kairo_kernel.contracts.identifiers import ToolCallId
    from kairo_kernel.contracts.json import JsonObject
    from kairo_kernel.contracts.tools import ToolExecutionStatus

    kernel = StreamingFakeKernel()
    invocation = ToolInvocation(
        ToolCallId("call-1"),
        TURN,
        SESSION,
        "list_directory",
        JsonObject.from_pairs(("path", ".")),
        OperationScope.INTERNAL,
    )
    result = ToolResult(
        ToolCallId("call-1"),
        "list_directory",
        ToolExecutionStatus.SUCCEEDED,
        (TextBlock("42 files"),),
        datetime.now(timezone.utc),
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    for event in (
        _tool_event(1, "requested", invocation),
        _tool_event(2, "started", invocation),
        _tool_event(3, "completed", invocation, result),
    ):
        await kernel.events.emit(event)
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        cards = [card for card in app.state.tool_cards if card.tool_call_id == "call-1"]
        assert len(cards) == 1
        assert cards[0].status == "completed"
        assert cards[0].result_status == "succeeded"


async def test_plan_card_is_structured() -> None:
    from kairo_tui.reducer import PlanCardUpdated, fold_event

    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        # Pre-feed a plan message event through the reducer.
        plan_id = MessageId("plan-1")
        event = _message_event(1, plan_id, "plan_delta", (TextBlock("1. Read files"),))
        state, actions = fold_event(app.state, event)
        assert any(isinstance(action, PlanCardUpdated) for action in actions)
        assert len(state.plan_cards) == 1
        assert state.plan_cards[0].blocks == (TextBlock("1. Read files"),)
        await pilot.pause()


async def test_stop_cancels_turn_and_marks_stopping() -> None:
    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        # A running turn exists before any stop request.
        await kernel.events.emit(_turn_event(1, TurnStatus.RUNNING, TurnPhase.STREAMING))
        for _ in range(3):
            await pilot.pause()
        assert app.state.stopping_turn_id is None
        app.action_stop()
        await pilot.pause()
        assert kernel.cancelled == [TURN]
        assert app.state.stopping_turn_id == TURN
        # Terminal event clears the stopping marker exactly once.
        await kernel.events.emit(_turn_event(2, TurnStatus.CANCELLED, None))
        for _ in range(3):
            await pilot.pause()
        assert app.state.stopping_turn_id is None


async def test_retry_uses_exact_last_user_text() -> None:
    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        await pilot.press("r", "e", "t", "r", "y")
        await pilot.press("enter")
        await pilot.pause()
        app.action_retry()
        await pilot.pause()
        assert kernel.submitted == ["retry", "retry"]


async def test_session_switch_keeps_transcripts_separate() -> None:
    from kairo_tui.reducer import TranscriptMerged

    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        app.dispatch_action(TranscriptMerged(SESSION, MessageId("m1"), "assistant", (TextBlock("a"),)))
        app.dispatch_action(TranscriptMerged(SessionId("session-2"), MessageId("m2"), "assistant", (TextBlock("b"),)))
        await pilot.pause()
        assert len(app.state.transcripts) == 2
        first = next(item for item in app.state.transcripts if item.session_id == SESSION)
        second = next(item for item in app.state.transcripts if item.session_id == SessionId("session-2"))
        assert first.entries[0].content == (TextBlock("a"),)
        assert second.entries[0].content == (TextBlock("b"),)


async def test_recovery_reloads_history() -> None:
    kernel = StreamingFakeKernel()
    app = KairoTuiApp(kernel=kernel)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        for _ in range(4):
            await pilot.pause()
        app._recover_state()
        await pilot.pause()
        await pilot.pause()
        transcript = next(item for item in app.state.transcripts if item.session_id == SESSION)
        assert any(entry.role == "user" for entry in transcript.entries)
