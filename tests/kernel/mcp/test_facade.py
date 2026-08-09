from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    EventType,
    InteractionAction,
    InteractionKind,
    ProviderStreamKind,
)
from kairo_kernel.contracts.events import ChangeEvent, KernelEvent
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.engine import EngineOptions
from kairo_kernel.mcp import McpClient, McpHub, McpProtocolError, McpServerConfig, McpServerTrustStore
from kairo_kernel.runtime.events import EventSubscription
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, session


class MemoryTransport:
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        method = message["method"]
        if method == "server/discover":
            result: dict[str, object] = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "resources/list":
            result = {"resources": [{"name": "guide", "uri": "file:///guide"}]}
        elif method == "prompts/list":
            result = {"prompts": [{"name": "review"}]}
        elif method == "tools/call":
            params = message.get("params", {})
            assert isinstance(params, dict)
            result = {"content": [{"type": "text", "text": f"echo:{params.get('arguments', {})}"}]}
        elif method == "resources/read":
            result = {"contents": [{"uri": "file:///guide", "text": "guide body"}]}
        elif method == "prompts/get":
            result = {"messages": [{"role": "user", "content": {"type": "text", "text": "review this"}}]}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    async def notify(self, message: dict[str, object]) -> None:
        return None

    async def close(self) -> None:
        return None


def _kernel(
    tmp_path: Path,
    *,
    mode: AuthorizationMode = AuthorizationMode.MANUAL,
    transport_factory: object | None = None,
    timeout_seconds: float = 30.0,
):
    config = McpServerConfig("server", "stdio", command="echo-server")
    store = McpServerTrustStore(tmp_path / "mcp.json")
    store.trust(config, config.digest)
    client = McpClient(config, store, transport_factory=transport_factory or (lambda server: MemoryTransport()))
    root = str(tmp_path)
    return build_kernel(
        KernelConfig(
            root,
            database_path=root + "/kernel.db",
            default_session_id=SessionId("session-1"),
            enable_builtin_tools=False,
            engine_options=EngineOptions(authorization_mode=mode),
            mcp_call_timeout_seconds=timeout_seconds,
        ),
        KernelDependencies(
            provider=FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),)),
            sessions=FakeSessions(session()),
            mcp=McpHub((client,)),
        ),
    )


def test_mcp_facade_typed_calls_and_tool_bridge(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO)
        async with kernel:
            connected = await kernel.mcp.connect()
            assert connected.ok

            called = await kernel.mcp.call_tool(
                "mcp__server__tools__echo", JsonObject.from_pairs(("text", "hello"))
            )
            assert called.ok and called.value is not None
            assert "echo:" in str(called.value.get("content"))

            read = await kernel.mcp.read_resource("mcp__server__resources__guide")
            assert read.ok and read.value is not None
            assert "guide body" in str(read.value.get("contents"))

            rendered = await kernel.mcp.render_prompt("mcp__server__prompts__review")
            assert rendered.ok and rendered.value is not None
            assert "review this" in str(rendered.value.get("messages"))

            tools = await kernel.tools.list()
            assert tools.ok and tools.value is not None
            assert "mcp__server__tools__echo" in [descriptor.name for descriptor in tools.value]

    asyncio.run(exercise())


def test_mcp_facade_unknown_names_and_lifecycle_gating(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        early = await kernel.mcp.call_tool("mcp__server__tools__echo")
        assert early.error is not None and early.error.code is ErrorCode.KERNEL_NOT_RUNNING

        async with kernel:
            await kernel.mcp.connect()
            unknown = await kernel.mcp.call_tool("mcp__server__tools__ghost")
            assert unknown.error is not None and unknown.error.code is ErrorCode.NOT_FOUND
            unknown_resource = await kernel.mcp.read_resource("mcp__server__resources__ghost")
            assert unknown_resource.error is not None and unknown_resource.error.code is ErrorCode.NOT_FOUND

    asyncio.run(exercise())


class RecordingTransport(MemoryTransport):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        self.calls.append(message["method"])
        return await super().request(message)


class HangingTransport(MemoryTransport):
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "tools/call":
            await asyncio.Future()  # never resolves
        return await super().request(message)


class ClosingTransport(MemoryTransport):
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "tools/call":
            raise McpProtocolError("MCP stdio server closed the stream.")
        return await super().request(message)


class FailingConnectTransport(MemoryTransport):
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "server/discover":
            raise McpProtocolError("MCP stdio server failed to start.")
        return await super().request(message)


class FailingRefreshTransport(MemoryTransport):
    def __init__(self) -> None:
        self.fail: bool = False

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if self.fail and message["method"] == "tools/list":
            raise OSError("MCP stdio pipe closed.")
        return await super().request(message)


async def _wait_for_pending(kernel) -> InteractionRequest:
    for _ in range(200):
        pending = await kernel.interactions.pending()
        if pending:
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no pending interaction")


async def _wait_for_config_changed(subscription: EventSubscription) -> KernelEvent:
    """Drain a live subscription until a CONFIG_CHANGED event arrives (skips interaction events)."""
    for _ in range(10):
        event = await asyncio.wait_for(subscription.receive(), timeout=5.0)
        if event.event_type is EventType.CONFIG_CHANGED:
            return event
    raise AssertionError("no CONFIG_CHANGED event received")


def test_mcp_facade_manual_blocks_until_approval_no_bypass(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(
                kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hello")))
            )
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            assert "tools/call" not in transport.calls  # no bypass: nothing reached the server yet
            receipt = await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            assert receipt.ok
            result = await task
            assert result.ok and result.value is not None
            assert "echo:" in str(result.value.get("content"))
            assert "tools/call" in transport.calls

    asyncio.run(exercise())


def test_mcp_facade_manual_reject_is_policy_denied(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.call_tool("mcp__server__tools__echo"))
            pending = await _wait_for_pending(kernel)
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.REJECT)
            )
            result = await task
            assert result.error is not None and result.error.code is ErrorCode.POLICY_DENIED
            assert "tools/call" not in transport.calls

    asyncio.run(exercise())


def test_mcp_facade_auto_enable_yolo_persists_mode(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.AUTO, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            subscription = await kernel.events.subscribe(
                after_sequence=(await kernel.events.snapshot()).newest_sequence
            )
            task = asyncio.create_task(
                kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hello")))
            )
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            assert "tools/call" not in transport.calls  # approval required before the server is reached
            receipt = await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.ENABLE_YOLO)
            )
            assert receipt.ok
            result = await task
            assert result.ok and result.value is not None
            assert "echo:" in str(result.value.get("content"))
            assert "tools/call" in transport.calls
            snapshot = await kernel.preferences.snapshot()
            assert snapshot.authorization_mode is AuthorizationMode.YOLO
            change = await _wait_for_config_changed(subscription)
            assert isinstance(change.payload, ChangeEvent)
            assert change.payload.revision == snapshot.revision  # post-bump preferences revision
            assert "yolo" in change.payload.summary
            await subscription.close()

    asyncio.run(exercise())


def test_mcp_facade_manual_enable_auto_persists_mode(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            subscription = await kernel.events.subscribe(
                after_sequence=(await kernel.events.snapshot()).newest_sequence
            )
            task = asyncio.create_task(
                kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hello")))
            )
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            receipt = await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.ENABLE_AUTO)
            )
            assert receipt.ok
            result = await task
            assert result.ok and result.value is not None
            assert "echo:" in str(result.value.get("content"))
            snapshot = await kernel.preferences.snapshot()
            assert snapshot.authorization_mode is AuthorizationMode.AUTO
            change = await _wait_for_config_changed(subscription)
            assert isinstance(change.payload, ChangeEvent)
            assert change.payload.revision == snapshot.revision  # post-bump preferences revision
            assert "auto" in change.payload.summary
            await subscription.close()

    asyncio.run(exercise())


def test_mcp_facade_yolo_executes_without_approval(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            called = await kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hi")))
            assert called.ok and called.value is not None
            assert "echo:" in str(called.value.get("content"))
            assert (await kernel.interactions.pending()) == ()  # no interaction was created

    asyncio.run(exercise())


def test_mcp_facade_auto_requires_approval_for_external_scope(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.AUTO, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.call_tool("mcp__server__tools__echo"))
            pending = await _wait_for_pending(kernel)
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            result = await task
            assert result.ok and result.value is not None

    asyncio.run(exercise())


def test_mcp_facade_reads_are_gated_in_manual_mode(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.read_resource("mcp__server__resources__guide"))
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            result = await task
            assert result.ok and "guide body" in str(result.value.get("contents"))

    asyncio.run(exercise())


def test_mcp_facade_call_tool_timeout_fails_closed(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(
            tmp_path,
            mode=AuthorizationMode.YOLO,
            transport_factory=lambda server: HangingTransport(),
            timeout_seconds=0.1,
        )
        async with kernel:
            await kernel.mcp.connect()
            result = await kernel.mcp.call_tool("mcp__server__tools__echo")
            assert result.error is not None and result.error.code is ErrorCode.RESOURCE_EXHAUSTED
            assert result.error.retryable

    asyncio.run(exercise())


def test_mcp_facade_disconnect_fails_closed(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO, transport_factory=lambda server: ClosingTransport())
        async with kernel:
            await kernel.mcp.connect()
            result = await kernel.mcp.call_tool("mcp__server__tools__echo")
            assert result.error is not None and result.error.code is ErrorCode.PROVIDER_CLIENT

    asyncio.run(exercise())


def test_mcp_facade_connect_transport_failure_returns_typed_error(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(
            tmp_path,
            mode=AuthorizationMode.YOLO,
            transport_factory=lambda server: FailingConnectTransport(),
        )
        async with kernel:
            result = await kernel.mcp.connect()
            assert result.error is not None
            assert result.error.code is ErrorCode.PROVIDER_CLIENT
            assert result.error.operation == "mcp.connect"

    asyncio.run(exercise())


def test_mcp_facade_refresh_transport_failure_returns_typed_error(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = FailingRefreshTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO, transport_factory=lambda server: transport)
        async with kernel:
            connected = await kernel.mcp.connect()
            assert connected.ok
            transport.fail = True
            result = await kernel.mcp.refresh()
            assert result.error is not None
            assert result.error.code is ErrorCode.PROVIDER_CONNECTION
            assert result.error.retryable
            assert result.error.operation == "mcp.refresh"

    asyncio.run(exercise())
