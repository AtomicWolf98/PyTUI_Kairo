from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel.contracts.enums import ErrorCode, OperationScope, ToolExecutionStatus
from kairo_kernel.contracts.identifiers import SessionId, ToolCallId, TurnId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.tools import ToolExecutionContext, ToolInvocation
from kairo_kernel.mcp import McpClient, McpHub, McpServerConfig, McpServerTrustStore
from kairo_kernel.tools.mcp import McpToolRegistry
from kairo_kernel.tools.registry import BuiltinToolRegistry, CompositeToolRegistry
from tests.kernel.engine.fakes import FakeTool


class MemoryTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        self.requests.append(message)
        method = message["method"]
        if method == "server/discover":
            result: dict[str, object] = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "Echo tool", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            params = message.get("params", {})
            assert isinstance(params, dict)
            result = {"content": [{"type": "text", "text": f"echo:{params.get('arguments', {})}"}]}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    async def notify(self, message: dict[str, object]) -> None:
        return None

    async def close(self) -> None:
        return None


class Cancellation:
    cancelled = False

    async def wait(self) -> None:
        return None


class Sink:
    async def write(self, chunk: object) -> None:
        return None


def _client(tmp_path: Path) -> McpClient:
    config = McpServerConfig("server", "stdio", command="echo-server")
    store = McpServerTrustStore(tmp_path / "mcp.json")
    store.trust(config, config.digest)
    return McpClient(config, store, transport_factory=lambda server: MemoryTransport())


def test_mcp_registry_lists_and_executes_catalog_tools(tmp_path: Path) -> None:
    async def exercise() -> None:
        hub = McpHub((_client(tmp_path),))
        await hub.connect_all()
        registry = McpToolRegistry(hub)

        descriptors = await registry.list()
        assert [descriptor.name for descriptor in descriptors] == ["mcp__server__tools__echo"]
        assert descriptors[0].source == "mcp"
        assert descriptors[0].permissions == ("mcp",)

        fetched = await registry.get("mcp__server__tools__echo")
        assert fetched.ok and fetched.value is not None
        tool = fetched.value
        invocation = ToolInvocation(
            ToolCallId("call-1"),
            TurnId("turn-1"),
            SessionId("session-1"),
            "mcp__server__tools__echo",
            JsonObject.from_pairs(("text", "hi")),
            OperationScope.EXTERNAL,
        )
        classified = await tool.classify(invocation)
        assert classified.ok and classified.value is OperationScope.EXTERNAL
        result = await tool.execute(invocation, ToolExecutionContext("C:/ws", "auto"), Cancellation(), Sink())
        assert result.status is ToolExecutionStatus.SUCCEEDED
        assert "echo:" in result.content[0].text

        missing = await registry.get("mcp__server__tools__nope")
        assert missing.error is not None and missing.error.code is ErrorCode.TOOL_NOT_FOUND

    asyncio.run(exercise())


def test_composite_registry_merges_and_dedupes_first_wins(tmp_path: Path) -> None:
    async def exercise() -> None:
        builtin = BuiltinToolRegistry((FakeTool("read_file"),))
        mcp = McpToolRegistry(McpHub((_client(tmp_path),)))
        composite = CompositeToolRegistry((builtin, mcp))

        names = [descriptor.name for descriptor in await composite.list()]
        assert names == ["read_file"]  # MCP server not connected yet: catalog empty

        duplicate = CompositeToolRegistry((builtin, BuiltinToolRegistry((FakeTool("read_file"),))))
        assert len(await duplicate.list()) == 1

        fetched = await composite.get("read_file")
        assert fetched.ok
        missing = await composite.get("ghost")
        assert missing.error is not None and missing.error.code is ErrorCode.TOOL_NOT_FOUND
        reloaded = await composite.reload()
        assert reloaded.ok

    asyncio.run(exercise())
