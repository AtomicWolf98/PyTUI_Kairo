from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    InteractionAction,
    MessageKind,
    MessageRole,
    OperationScope,
    ToolExecutionStatus,
)
from kairo_kernel.contracts.identifiers import MessageId, ProfileId, SessionId
from kairo_kernel.contracts.interactions import InteractionReceipt, InteractionRequest, InteractionResponse
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.contracts.support import SessionRecord, SessionSummary
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolOutputChunk,
    ToolResult,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolOutputSink, ToolPort

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
PROFILE = ProviderProfile(ProfileId("provider/model"), "Model", "provider", "model", "https://test", 32000, 1000, 0.2)


def session(identifier: str = "session-1") -> SessionRecord:
    return SessionRecord(
        SessionId(identifier),
        identifier,
        (Message(MessageId(f"system-{identifier}"), MessageRole.SYSTEM, MessageKind.CHAT, (TextBlock("system"),)),),
        NOW,
        NOW,
    )


class FakeSessions:
    def __init__(self, *records: SessionRecord):
        self.records = {record.session_id: record for record in records}
        self.saves: list[tuple[SessionRecord, bool]] = []
        self.fail_save = False

    async def list(self) -> tuple[SessionSummary, ...]:
        return tuple(
            SessionSummary(item.session_id, item.name, len(item.messages), item.created_at, item.updated_at)
            for item in self.records.values()
        )

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        record = self.records.get(session_id)
        if record is None:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "missing"))
        return KernelResult.success(record)

    async def save(self, record: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        if self.fail_save:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_PERSISTENCE_FAILED, "save failed"))
        self.records[record.session_id] = record
        self.saves.append((record, active))
        return KernelResult.success(record)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        return KernelResult.success(self.records.pop(session_id, None) is not None)


class FakeProvider:
    def __init__(self, *scripts: tuple[ProviderStreamEvent, ...], profile: ProviderProfile = PROFILE):
        self.scripts = list(scripts)
        self.requests: list[ProviderRequest] = []
        self.block = False
        self.profile = profile

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        return KernelResult.success(self.profile)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        return KernelResult.success(self.profile)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        self.requests.append(request)
        return self._stream(cancellation)

    async def _stream(self, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        if self.block:
            await cancellation.wait()
            return
        script = self.scripts.pop(0)
        for event in script:
            await asyncio.sleep(0)
            yield event


class FakeTool:
    def __init__(self, name: str, *, raises: bool = False):
        self._descriptor = ToolDescriptor(name, name, JsonObject(), ("read",))
        self.raises = raises
        self.calls: list[ToolInvocation] = []

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]:
        return KernelResult.success(OperationScope.INTERNAL)

    async def execute(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
    ) -> ToolResult:
        self.calls.append(invocation)
        if self.raises:
            raise RuntimeError("tool exploded")
        await output.write(ToolOutputChunk(invocation.tool_call_id, 1, (TextBlock("chunk"),)))
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.SUCCEEDED,
            (TextBlock(f"result-{invocation.name}"),),
            NOW,
            NOW,
        )


class FakeTools:
    def __init__(self, *tools: FakeTool):
        self.tools = {tool.descriptor.name: tool for tool in tools}

    async def list(self) -> tuple[ToolDescriptor, ...]:
        return tuple(tool.descriptor for tool in self.tools.values())

    async def get(self, name: str) -> KernelResult[ToolPort]:
        tool = self.tools.get(name)
        if tool is None:
            return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, "missing"))
        return KernelResult.success(tool)

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        return KernelResult.success(await self.list())


class FakeAuthorization:
    def __init__(self, allowed: bool = True):
        self.allowed = allowed

    async def is_authorized(self, mode: AuthorizationMode, scope: OperationScope) -> bool:
        return self.allowed


class FakeInteractions:
    def __init__(self, *responses: tuple[InteractionAction, str]):
        self.responses = list(responses)
        self.requests: list[InteractionRequest] = []

    async def request(self, request: InteractionRequest, cancellation: CancellationToken) -> InteractionResponse:
        self.requests.append(request)
        action, text = self.responses.pop(0) if self.responses else (request.safe_default, "")
        return InteractionResponse(request.interaction_id, request.turn_id, action, text)

    async def respond(self, response: InteractionResponse) -> KernelResult[InteractionReceipt]:
        return KernelResult.success(InteractionReceipt(response.interaction_id, response.turn_id, True))
