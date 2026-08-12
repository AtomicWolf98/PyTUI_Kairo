from __future__ import annotations

import asyncio
import inspect
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from kairo_kernel.contracts.commands import CommandArgument, CommandOutcome, KernelCommand, ParsedCommand
from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    Message,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    EventType,
    InteractionAction,
    InteractionKind,
    LifecycleState,
    MessageKind,
    MessageRole,
    OperationScope,
    ProviderFailureKind,
    ProviderStreamKind,
    ToolExecutionStatus,
    TurnPhase,
    TurnStatus,
)
from kairo_kernel.contracts.events import (
    ChangeEvent,
    EventPayload,
    EventReplay,
    InteractionEvent,
    KernelEvent,
    LifecycleEvent,
    MessageEvent,
    NoticeEvent,
    ToolEvent,
    TurnEvent,
    UsageEvent,
)
from kairo_kernel.contracts.identifiers import (
    EventId,
    InteractionId,
    KernelId,
    MemoryId,
    MessageId,
    ProfileId,
    ResourceId,
    SecretId,
    SessionId,
    SpanId,
    ToolCallId,
    TraceId,
    TurnId,
)
from kairo_kernel.contracts.interactions import (
    InteractionChoice,
    InteractionReceipt,
    InteractionRequest,
    InteractionResponse,
)
from kairo_kernel.contracts.json import Contract, JsonArray, JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.lifecycle import (
    ContextStats,
    KernelCapabilities,
    KernelStatus,
    ShutdownReport,
    ShutdownRequest,
)
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
from kairo_kernel.contracts.providers import (
    ProviderFailure,
    ProviderProfile,
    ProviderRequest,
    ProviderStreamEvent,
    ProviderUsage,
)
from kairo_kernel.contracts.support import (
    ConfigSnapshot,
    LogRecord,
    MemoryEntry,
    MemoryQuery,
    MetricRecord,
    PromptDescriptor,
    PromptRenderRequest,
    PromptRenderResult,
    ResourceDescriptor,
    ResourceRead,
    SecretDescriptor,
    SecretInput,
    SessionRecord,
    SessionSummary,
    SpanRecord,
    TraceContext,
    WorkspaceRecord,
)
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolOutputChunk,
    ToolResult,
)
from kairo_kernel.contracts.turns import CancelReceipt, TurnAccepted, TurnRequest, TurnResult, TurnSnapshot
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.providers import ProviderPort
from kairo_kernel.ports.repositories import SessionRepositoryPort

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
JSON = JsonObject.from_pairs(("string", "value"), ("array", JsonArray((1, True, None))))
TEXT = TextBlock("hello")
CALL = ToolCallBlock(ToolCallId("call-1"), "read_file", JSON)
TOOL_RESULT_BLOCK = ToolResultBlock(
    ToolCallId("call-1"), "read_file", ToolExecutionStatus.SUCCEEDED, (TEXT,)
)
MESSAGE = Message(MessageId("message-1"), MessageRole.ASSISTANT, MessageKind.CHAT, (TEXT, CALL))
PROFILE = ProviderProfile(ProfileId("p/m"), "Profile", "p", "m", "https://example.test/v1", 32000, 1000, 0.2)
TOOL = ToolDescriptor("read_file", "Read", JSON, ("read",))
INVOCATION = ToolInvocation(
    ToolCallId("call-1"), TurnId("turn-1"), SessionId("session-1"), "read_file", JSON, OperationScope.INTERNAL
)
RESULT = ToolResult(ToolCallId("call-1"), "read_file", ToolExecutionStatus.SUCCEEDED, (TEXT,), NOW, NOW)
CHOICE = InteractionChoice(InteractionAction.REJECT, "Reject")
INTERACTION = InteractionRequest(
    InteractionId("interaction-1"),
    TurnId("turn-1"),
    SessionId("session-1"),
    InteractionKind.TOOL_APPROVAL,
    "Run?",
    (InteractionChoice(InteractionAction.APPROVE_ONCE, "Run once"), CHOICE),
    NOW,
    InteractionAction.REJECT,
)
CONTEXT = ContextStats(100, 1000, 10.0, 80, 20)
TRACE = TraceContext(TraceId("trace-1"), SpanId("span-1"))


def _specimens() -> tuple[Contract, ...]:
    content = (
        TEXT,
        ReasoningBlock("reason"),
        ImageBlock("image/png", "file:///image.png", alt_text="image"),
        AudioBlock("audio/wav", "file:///audio.wav", transcript="audio"),
        FileBlock("a.txt", "text/plain", "file:///a.txt", 3, "abc"),
        ResourceBlock(ResourceId("resource-1"), "resource://one", "one"),
        CALL,
        TOOL_RESULT_BLOCK,
        MESSAGE,
    )
    provider = (
        PROFILE,
        ProviderRequest(PROFILE, (MESSAGE,), (TOOL,)),
        ProviderUsage(10, 4, 2),
        ProviderFailure(ProviderFailureKind.RATE_LIMIT, "slow", True, 429),
        ProviderStreamEvent(ProviderStreamKind.CONTENT, (TEXT,)),
        ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=CALL),
        ProviderStreamEvent(ProviderStreamKind.USAGE, usage=ProviderUsage(1, 2)),
        ProviderStreamEvent(
            ProviderStreamKind.FAILED,
            failure=ProviderFailure(ProviderFailureKind.CONNECTION, "offline", True),
        ),
    )
    tools = (
        TOOL,
        INVOCATION,
        ToolExecutionContext("C:/workspace", AuthorizationMode.MANUAL.value),
        ToolOutputChunk(ToolCallId("call-1"), 1, (TEXT,)),
        RESULT,
    )
    lifecycle = (
        KernelCapabilities(features=("turns",)),
        CONTEXT,
        KernelStatus(
            KernelId("kernel-1"),
            LifecycleState.RUNNING,
            "1.0",
            NOW,
            "C:/workspace",
            1,
            ProfileId("p/m"),
            SessionId("session-1"),
            TurnId("turn-1"),
            AuthorizationMode.MANUAL,
            False,
            True,
            CONTEXT,
        ),
        ShutdownRequest(),
        ShutdownReport(LifecycleState.STOPPED, True, ("tools",)),
    )
    turns = (
        TurnRequest("hello", SessionId("session-1"), "client-1", JSON),
        TurnAccepted(TurnId("turn-1"), SessionId("session-1"), NOW),
        TurnSnapshot(TurnId("turn-1"), SessionId("session-1"), TurnStatus.RUNNING, TurnPhase.STREAMING, NOW, None),
        TurnResult(TurnId("turn-1"), SessionId("session-1"), TurnStatus.SUCCEEDED, (MESSAGE,), NOW, NOW),
        CancelReceipt(TurnId("turn-1"), True),
    )
    interactions = (
        CHOICE,
        INTERACTION,
        InteractionResponse(InteractionId("interaction-1"), TurnId("turn-1"), InteractionAction.REJECT),
        InteractionReceipt(InteractionId("interaction-1"), TurnId("turn-1"), True),
    )
    support = (
        SessionRecord(SessionId("session-1"), "Session", (MESSAGE,), NOW, NOW),
        SessionSummary(SessionId("session-1"), "Session", 1, NOW, NOW, 100),
        ConfigSnapshot(1, JSON),
        WorkspaceRecord("C:/workspace", 1),
        MemoryEntry(MemoryId("memory-1"), "user", "key", (TEXT,), NOW, NOW, ("tag",)),
        MemoryQuery("user", "query"),
        SecretDescriptor(SecretId("secret-1"), "environment", "***", True),
        SecretInput(SecretId("secret-1"), "top-secret"),
        ResourceDescriptor(ResourceId("resource-1"), "resource://one", "one", "text/plain", 5, "abc", JSON),
        ResourceRead(ResourceDescriptor(ResourceId("resource-1"), "resource://one", "one", "text/plain"), (TEXT,)),
        PromptDescriptor("review", "Review", JSON),
        PromptRenderRequest("review", JSON),
        PromptRenderResult((MESSAGE,)),
        TRACE,
        LogRecord(NOW, "info", "message", JSON, TRACE),
        MetricRecord("tokens", 1.0, NOW, "count", JSON),
        SpanRecord(TRACE, "turn", NOW, NOW, JSON),
    )
    event_payloads: tuple[EventPayload, ...] = (
        LifecycleEvent(LifecycleState.RUNNING),
        TurnEvent(TurnStatus.RUNNING, TurnPhase.STREAMING),
        MessageEvent(MessageId("message-1"), "delta", (TEXT,)),
        ToolEvent("completed", INVOCATION, result=RESULT),
        InteractionEvent("requested", request=INTERACTION),
        UsageEvent(CONTEXT),
        ChangeEvent(1, "session-1", "changed"),
        NoticeEvent("warning", "notice", JSON),
    )
    events = tuple(
        KernelEvent(
            EventId(f"event-{index}"),
            KernelId("kernel-1"),
            index,
            NOW,
            EventType.NOTICE,
            payload,
            turn_id=TurnId("turn-1"),
            session_id=SessionId("session-1"),
        )
        for index, payload in enumerate(event_payloads, 1)
    )
    errors = (
        KernelError(code=ErrorCode.INTERNAL, message="failed"),
        KernelResult.success(TurnAccepted(TurnId("turn-1"), SessionId("session-1"), NOW)),
    )
    commands = (
        CommandArgument("path", required=True, greedy=True),
        KernelCommand(
            "/run",
            "Run a task",
            "Run a task with one argument",
            (CommandArgument("path", required=True),),
            mutates=True,
            needs_session=True,
        ),
        ParsedCommand("/run", ("task",)),
        CommandOutcome("/run", "done", SessionId("session-1")),
    )
    preferences = (
        PreferencesSnapshot(0, authorization_mode=AuthorizationMode.AUTO, plan_mode=True, profile_id=ProfileId("p/m")),
        PreferencesPatch(0, plan_mode=True, clear_profile_id=True),
    )
    return content + provider + tools + lifecycle + turns + interactions + support + event_payloads + events + (EventReplay(events, 1, len(events)),) + errors + commands + preferences


@pytest.mark.parametrize("specimen", _specimens(), ids=lambda value: type(value).__name__)
def test_every_contract_round_trips(specimen: Contract) -> None:
    assert type(specimen).from_json(specimen.to_json()) == specimen


def test_contract_enum_round_trips_as_enum_not_str() -> None:
    message = Message(MessageId("message-1"), MessageRole.USER, MessageKind.CHAT, (TEXT,))
    decoded_value = Message.from_json_value(message.to_json_value())
    assert decoded_value.role is MessageRole.USER
    assert decoded_value.kind is MessageKind.CHAT
    decoded = Message.from_json(message.to_json())
    assert decoded.role is MessageRole.USER
    assert decoded.kind is MessageKind.CHAT


def test_contract_enum_encodes_with_enum_marker() -> None:
    encoded = Message(MessageId("message-1"), MessageRole.USER, MessageKind.CHAT, (TEXT,)).to_json_value()
    role = encoded.get("role")
    assert isinstance(role, JsonObject)
    assert role.get("$enum") == "kairo_kernel.contracts.enums.MessageRole"
    assert role.get("value") == "user"


def test_plain_str_round_trips_as_str() -> None:
    text = TextBlock("hello")
    decoded = TextBlock.from_json_value(text.to_json_value())
    assert type(decoded.text) is str
    assert decoded.text == "hello"


def test_contracts_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        cast(object, TEXT).__setattr__("text", "changed")


def test_secret_serialization_is_redacted() -> None:
    secret = SecretInput(SecretId("secret-1"), "top-secret")
    payload = secret.to_json()
    assert "top-secret" not in payload
    assert "[REDACTED]" in payload
    assert SecretInput.from_json(payload) == secret
    assert SecretInput.from_json(payload).value == ""


def test_json_freeze_is_immutable_and_reversible() -> None:
    value = {"one": [1, True, None], "two": "value"}
    assert thaw_json(freeze_json(value)) == value


class _Cancellation:
    cancelled = False

    async def wait(self) -> None:
        return None


class _Provider:
    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        return KernelResult.success(PROFILE)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        return KernelResult.success(PROFILE)

    async def _events(self):
        yield ProviderStreamEvent(ProviderStreamKind.COMPLETED)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken):
        return self._events()


class _Sessions:
    async def list(self) -> tuple[SessionSummary, ...]:
        return ()

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        return KernelResult.success(session)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        return KernelResult.success(True)


def test_fake_protocols_are_usable() -> None:
    provider: ProviderPort = _Provider()
    sessions: SessionRepositoryPort = _Sessions()

    async def exercise() -> None:
        resolved = await provider.resolve_profile(None, "chat")
        assert resolved.value == PROFILE
        stream = provider.stream(ProviderRequest(PROFILE, (MESSAGE,)), _Cancellation())
        assert [event.kind async for event in stream] == [ProviderStreamKind.COMPLETED]
        assert await sessions.list() == ()

    asyncio.run(exercise())


def test_kernel_imports_are_ui_and_framework_free() -> None:
    root = Path(__file__).parents[3] / "kairo_kernel"
    forbidden = ("import rich", "import textual", "import fastapi", "agent.ui", "agent.web", "tui_widgets")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert not any(token in source for token in forbidden)
    assert "typing import Any" not in source


def test_public_import_smoke() -> None:
    import kairo_kernel

    assert kairo_kernel.contracts.KERNEL_API_VERSION == "1.1"
    assert inspect.isclass(kairo_kernel.KernelError)
    assert inspect.isclass(kairo_kernel.contracts.commands.KernelCommand)
    assert inspect.isclass(kairo_kernel.contracts.commands.CommandOutcome)
    assert inspect.isclass(kairo_kernel.contracts.preferences.PreferencesPatch)
    assert inspect.isclass(kairo_kernel.contracts.preferences.PreferencesSnapshot)
    assert inspect.isclass(kairo_kernel.ports.preferences.PreferencesPort)
    assert kairo_kernel.ports.PreferencesPort is kairo_kernel.ports.preferences.PreferencesPort
    assert inspect.isclass(kairo_kernel.KernelOpenOptions)
    assert inspect.isclass(kairo_kernel.OpenedKernel)
    assert inspect.iscoroutinefunction(kairo_kernel.open_kernel)
    assert inspect.isclass(kairo_kernel.contracts.providers.ProviderConnectionRequest)
    assert inspect.isclass(kairo_kernel.contracts.providers.ProviderConnectionReceipt)
