from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kairo_kernel.mcp import (
    HttpResponse,
    McpClient,
    McpError,
    McpHub,
    McpProtocolError,
    McpServerConfig,
    McpServerTrustStore,
    McpTrustError,
    StdioTransport,
    StreamableHttpTransport,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "mcp"


class MemoryTransport:
    def __init__(self, *, fail_call_once: bool = False):
        self.requests: list[dict[str, object]] = []
        self.notifications: list[dict[str, object]] = []
        self.closed = False
        self.fail_call_once = fail_call_once

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        self.requests.append(message)
        method = message["method"]
        if method == "tools/call" and self.fail_call_once:
            self.fail_call_once = False
            raise ConnectionError("disconnected")
        params = message.get("params", {})
        assert isinstance(params, dict)
        if method == "server/discover":
            result = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        elif method == "initialize":
            result: dict[str, object] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        elif method == "tools/list":
            result = (
                {"tools": [{"name": "second", "inputSchema": {}}]}
                if params.get("cursor") == "next"
                else {"tools": [{"name": "first", "inputSchema": {}}], "nextCursor": "next"}
            )
        elif method == "resources/list":
            result = {"resources": [{"name": "guide", "uri": "file:///guide"}]}
        elif method == "prompts/list":
            result = {"prompts": [{"name": "review"}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}], "arguments": params.get("arguments", {})}
        elif method == "resources/read":
            result = {"contents": [{"uri": params.get("uri"), "text": "guide"}]}
        elif method == "prompts/get":
            result = {"messages": [], "arguments": params.get("arguments", {})}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    async def notify(self, message: dict[str, object]) -> None:
        self.notifications.append(message)

    async def close(self) -> None:
        self.closed = True


class Sender:
    def __init__(self, *responses: HttpResponse):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, tuple[tuple[str, str], ...], bytes]] = []

    async def send(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


class InputRequiredTransport(MemoryTransport):
    def __init__(self, request_type: str):
        super().__init__()
        self.request_type = request_type

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "resultType": "input_required",
                    "inputRequests": {"blocked": {"type": self.request_type}},
                },
            }
        return await super().request(message)


def config(name: str = "docs") -> McpServerConfig:
    return McpServerConfig(name, "http", url="https://mcp.example/rpc")


def trusted_client(tmp_path, server_config=None, factory=None):
    server_config = server_config or config()
    store = McpServerTrustStore(tmp_path / f"{server_config.name}-trust.json")
    store.trust(server_config, server_config.digest)
    return McpClient(server_config, store, factory), store


@pytest.mark.asyncio
async def test_untrusted_or_changed_server_configuration_cannot_connect(tmp_path):
    original = config()
    store = McpServerTrustStore(tmp_path / "trust.json")
    client = McpClient(original, store, lambda _: MemoryTransport())

    with pytest.raises(McpTrustError):
        await client.connect()

    store.trust(original, original.digest)
    changed = McpServerConfig("docs", "http", url="https://other.example/rpc")
    with pytest.raises(McpTrustError):
        await McpClient(changed, store, lambda _: MemoryTransport()).connect()


@pytest.mark.asyncio
async def test_connect_handshake_paginates_and_namespaces_all_catalogs(tmp_path):
    transport = MemoryTransport()
    client, _ = trusted_client(tmp_path, factory=lambda _: transport)

    catalog = await client.connect()

    assert [item.qualified_name for item in catalog.tools] == [
        "mcp__docs__tools__first",
        "mcp__docs__tools__second",
    ]
    assert catalog.resources[0].qualified_name == "mcp__docs__resources__guide"
    assert catalog.prompts[0].qualified_name == "mcp__docs__prompts__review"
    assert transport.notifications == []
    discover_params = transport.requests[0]["params"]
    assert isinstance(discover_params, dict)
    assert discover_params["_meta"] == {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "kairo-kernel", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


@pytest.mark.asyncio
async def test_tool_resource_and_prompt_calls_use_local_wire_names(tmp_path):
    transport = MemoryTransport()
    client, _ = trusted_client(tmp_path, factory=lambda _: transport)
    catalog = await client.connect()

    tool = await client.call_tool(catalog.tools[0].qualified_name, {"value": 1})
    resource = await client.read_resource(catalog.resources[0].qualified_name)
    prompt = await client.get_prompt(catalog.prompts[0].qualified_name, {"topic": "x"})

    assert tool["arguments"] == {"value": 1}
    assert resource["contents"] == [{"uri": "file:///guide", "text": "guide"}]
    assert prompt["arguments"] == {"topic": "x"}


@pytest.mark.asyncio
async def test_connection_failure_reconnects_revalidates_trust_and_retries(tmp_path):
    transports = [MemoryTransport(fail_call_once=True), MemoryTransport()]
    client, _ = trusted_client(tmp_path, factory=lambda _: transports.pop(0))
    catalog = await client.connect()

    result = await client.call_tool(catalog.tools[0].qualified_name, {})

    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert transports == []


@pytest.mark.asyncio
async def test_hub_keeps_same_local_names_isolated_by_server_namespace(tmp_path):
    first, _ = trusted_client(tmp_path, config("one"), lambda _: MemoryTransport())
    second, _ = trusted_client(tmp_path, config("two"), lambda _: MemoryTransport())
    hub = McpHub((first, second))
    await hub.connect_all()

    names = [entry.qualified_name for entry in hub.catalog() if entry.namespace == "tools"]

    assert "mcp__one__tools__first" in names
    assert "mcp__two__tools__first" in names
    await hub.close()


@pytest.mark.asyncio
async def test_streamable_http_tracks_session_parses_sse_rejects_sampling_and_deletes_session():
    server_request = b'data: {"jsonrpc":"2.0","id":"sample","method":"sampling/createMessage","params":{}}\n\n'
    result = b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    sender = Sender(
        HttpResponse(200, (("content-type", "text/event-stream"), ("mcp-session-id", "session-1")), server_request + result),
        HttpResponse(202, (), b""),
        HttpResponse(204, (), b""),
    )
    legacy = McpServerConfig("docs", "http", url="https://mcp.example/rpc", protocol_version="2025-06-18")
    transport = StreamableHttpTransport(legacy, sender)

    response = await transport.request({"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}})
    await transport.close()

    assert response["result"] == {"ok": True}
    assert sender.calls[1][0] == "POST"
    assert b'"code":-32000' in sender.calls[1][3]
    assert sender.calls[2][0] == "DELETE"
    assert ("mcp-session-id", "session-1") in sender.calls[2][2]


@pytest.mark.asyncio
async def test_modern_streamable_http_sends_routing_headers_and_stays_stateless():
    sender = Sender(
        HttpResponse(
            200,
            (("content-type", "application/json"), ("mcp-session-id", "must-be-ignored")),
            b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
        )
    )
    transport = StreamableHttpTransport(config(), sender)

    await transport.request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search", "arguments": {}}}
    )
    await transport.close()

    headers = dict(sender.calls[0][2])
    assert headers["mcp-protocol-version"] == "2026-07-28"
    assert headers["mcp-method"] == "tools/call"
    assert headers["mcp-name"] == "search"
    assert transport.session_id == ""
    assert len(sender.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("request_type", ["sampling", "elicitation"])
async def test_modern_multi_round_trip_sampling_and_elicitation_are_rejected(tmp_path, request_type):
    transport = InputRequiredTransport(request_type)
    client, _ = trusted_client(tmp_path, factory=lambda _: transport)
    catalog = await client.connect()

    with pytest.raises(McpError, match="disabled"):
        await client.call_tool(catalog.tools[0].qualified_name, {})


@pytest.mark.asyncio
async def test_stdio_transport_uses_env_allowlist_and_rejects_sampling(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFE_VALUE", "visible")
    monkeypatch.setenv("NOT_ALLOWED", "secret")
    server = FIXTURES / "stdio_server.py"
    server_config = McpServerConfig(
        "stdio",
        "stdio",
        command=sys.executable,
        arguments=(str(server),),
        environment_allowlist=("SAFE_VALUE",),
    )
    client, _ = trusted_client(tmp_path, server_config, lambda value: StdioTransport(value))
    catalog = await client.connect()

    result = await client.call_tool(catalog.tools[0].qualified_name, {})

    assert result["rejectionCode"] == -32000
    assert result["content"] == [{"type": "text", "text": "visible"}]
    await client.close()


@pytest.mark.asyncio
async def test_stdio_config_rejects_configured_environment_outside_allowlist():
    server_config = McpServerConfig(
        "bad",
        "stdio",
        command=sys.executable,
        environment=(("SECRET", "value"),),
    )
    transport = StdioTransport(server_config)

    with pytest.raises(McpProtocolError, match="not allowlisted"):
        await transport.start()
