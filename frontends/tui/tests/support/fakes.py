"""TUI-local fakes. Implement public kernel ports only; never import tests/kernel."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ErrorCode, OperationScope, ProviderStreamKind, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.contracts.tools import ToolDescriptor, ToolInvocation, ToolResult
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolPort

NOW_PROFILE = ProviderProfile(
    ProfileId("fake/model"),
    "Fake / model",
    "fake",
    "model",
    "https://fake.invalid/v1",
    32000,
    1000,
    0.2,
)


class FakeProvider:
    """Public ProviderPort implementation; never touches the network."""

    def __init__(
        self,
        *scripts: tuple[ProviderStreamEvent, ...],
        delay: float = 0.0,
        block: bool = False,
        profile: ProviderProfile = NOW_PROFILE,
    ):
        self.scripts = list(scripts) or [(ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED),)]
        self.delay = delay
        self.block = block
        self.profile = profile
        self.requests: list[ProviderRequest] = []

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
        if self.delay:
            with suppress(TimeoutError):
                await asyncio.wait_for(cancellation.wait(), timeout=self.delay)
        for event in self.scripts.pop(0) if self.scripts else ():
            await asyncio.sleep(0)
            yield event


class FakeTool:
    def __init__(self, name: str, *, result_status: ToolExecutionStatus = ToolExecutionStatus.SUCCEEDED) -> None:
        self.name = name
        self.calls = 0
        self.result_status = result_status
        self._descriptor = ToolDescriptor(name, name, JsonObject(), ("execute",))

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]:
        return KernelResult.success(OperationScope.EXTERNAL)

    async def execute(self, invocation, context, cancellation, output) -> ToolResult:
        self.calls += 1
        now = datetime.now(timezone.utc)
        return ToolResult(invocation.tool_call_id, self.name, self.result_status,
                          (TextBlock("tool ok"),), now, now)


class FakeToolRegistry:
    def __init__(self, *tools: FakeTool) -> None:
        self.by_name = {tool.name: tool for tool in tools}

    async def list(self) -> tuple[ToolDescriptor, ...]:
        return tuple(tool.descriptor for tool in self.by_name.values())

    async def get(self, name: str) -> KernelResult[ToolPort]:
        tool = self.by_name.get(name)
        if tool is None:
            return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, f"No tool {name}."))
        return KernelResult.success(tool)

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        return KernelResult.success(tuple(tool.descriptor for tool in self.by_name.values()))


class GatedProvider(FakeProvider):
    """Turn-completion gate for deterministic tests: ``started`` fires when the
    stream begins; the stream does not finish (no terminal event) until
    ``release`` is set. The cancellation token is still observed so teardown
    never hangs when a turn is cancelled before release."""

    def __init__(
        self,
        *scripts: tuple[ProviderStreamEvent, ...],
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        super().__init__(*scripts or ((ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED),),))
        self.started = started
        self.release = release

    async def _stream(self, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        self.started.set()
        release_task = asyncio.ensure_future(self.release.wait())
        cancel_task = asyncio.ensure_future(cancellation.wait())
        done, _ = await asyncio.wait({release_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        if release_task not in done:
            return  # cancelled before release: end the stream without completing the turn
        for event in self.scripts.pop(0) if self.scripts else ():
            await asyncio.sleep(0)
            yield event
