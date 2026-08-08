"""Trusted MCP client with handshake, reconnect and namespaced catalogs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from kairo_kernel.mcp.models import (
    PROTOCOL_VERSION,
    CatalogEntry,
    McpCatalog,
    McpError,
    McpProtocolError,
    McpServerConfig,
    McpTrustError,
    qualified_name,
)
from kairo_kernel.mcp.transport import McpTransport, StdioTransport, StreamableHttpTransport
from kairo_kernel.mcp.trust import McpServerTrustStore

TransportFactory = Callable[[McpServerConfig], McpTransport]


class McpClient:
    def __init__(
        self,
        config: McpServerConfig,
        trust_store: McpServerTrustStore,
        transport_factory: TransportFactory | None = None,
    ):
        self.config = config
        self.trust_store = trust_store
        self._factory = transport_factory or _default_transport
        self._transport: McpTransport | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self.server_capabilities: dict[str, object] = {}
        self.catalog = McpCatalog(config.name)

    @property
    def connected(self) -> bool:
        return self._transport is not None

    async def connect(self) -> McpCatalog:
        async with self._lock:
            await self._connect_locked()
            return await self._refresh_locked()

    async def reconnect(self) -> McpCatalog:
        async with self._lock:
            await self._close_locked()
            await self._connect_locked()
            return await self._refresh_locked()

    async def refresh(self) -> McpCatalog:
        async with self._lock:
            self._require_connected()
            return await self._refresh_locked()

    async def call_tool(self, qualified: str, arguments: dict[str, object]) -> dict[str, object]:
        local = self._local_name(qualified, "tools")
        return await self._rpc_with_reconnect("tools/call", {"name": local, "arguments": arguments})

    async def read_resource(self, qualified: str) -> dict[str, object]:
        entry = self._entry(qualified, self.catalog.resources)
        uri = entry.raw.get("uri")
        if not isinstance(uri, str):
            raise McpProtocolError(f"MCP resource '{qualified}' has no URI.")
        return await self._rpc_with_reconnect("resources/read", {"uri": uri})

    async def get_prompt(self, qualified: str, arguments: dict[str, object]) -> dict[str, object]:
        local = self._local_name(qualified, "prompts")
        return await self._rpc_with_reconnect("prompts/get", {"name": local, "arguments": arguments})

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()
            self.catalog = McpCatalog(self.config.name)

    async def _connect_locked(self) -> None:
        if not self.trust_store.is_trusted(self.config):
            raise McpTrustError(f"MCP server '{self.config.name}' is not trusted or its configuration changed.")
        transport = self._factory(self.config)
        self._transport = transport
        try:
            if self.config.protocol_version >= PROTOCOL_VERSION:
                result = await self._rpc_locked("server/discover", {})
                versions = result.get("supportedVersions")
                if not isinstance(versions, list) or self.config.protocol_version not in versions:
                    raise McpProtocolError(
                        f"MCP server does not support protocol version {self.config.protocol_version}."
                    )
            else:
                result = await self._rpc_locked(
                    "initialize",
                    {
                        "protocolVersion": self.config.protocol_version,
                        "capabilities": {},
                        "clientInfo": {"name": "kairo-kernel", "version": "1"},
                    },
                )
                protocol = result.get("protocolVersion")
                if protocol != self.config.protocol_version:
                    raise McpProtocolError(f"Unsupported MCP protocol version: {protocol}")
            capabilities = result.get("capabilities", {})
            if not isinstance(capabilities, dict):
                raise McpProtocolError("MCP initialize capabilities must be an object.")
            self.server_capabilities = capabilities
            if self.config.protocol_version < PROTOCOL_VERSION:
                await transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            await self._close_locked()
            raise

    async def _refresh_locked(self) -> McpCatalog:
        tools = await self._list_locked("tools", "tools") if "tools" in self.server_capabilities else ()
        resources = await self._list_locked("resources", "resources") if "resources" in self.server_capabilities else ()
        prompts = await self._list_locked("prompts", "prompts") if "prompts" in self.server_capabilities else ()
        self.catalog = McpCatalog(self.config.name, tools, resources, prompts)
        return self.catalog

    async def _list_locked(self, namespace: str, result_key: str) -> tuple[CatalogEntry, ...]:
        entries: list[CatalogEntry] = []
        cursor = ""
        while True:
            params: dict[str, object] = {"cursor": cursor} if cursor else {}
            result = await self._rpc_locked(f"{namespace}/list", params)
            raw_entries = result.get(result_key, [])
            if not isinstance(raw_entries, list):
                raise McpProtocolError(f"MCP {namespace}/list returned an invalid catalog.")
            for raw in raw_entries:
                if not isinstance(raw, dict):
                    raise McpProtocolError(f"MCP {namespace} catalog entry must be an object.")
                local = raw.get("name")
                if namespace == "resources" and not isinstance(local, str):
                    local = raw.get("uri")
                if not isinstance(local, str) or not local:
                    raise McpProtocolError(f"MCP {namespace} catalog entry has no name.")
                entries.append(CatalogEntry(namespace, local, qualified_name(self.config.name, namespace, local), raw))
            next_cursor = result.get("nextCursor", "")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        entries.sort(key=lambda item: item.qualified_name)
        qualified = [entry.qualified_name for entry in entries]
        if len(set(qualified)) != len(qualified):
            raise McpProtocolError(f"MCP {namespace} catalog contains duplicate names.")
        return tuple(entries)

    async def _rpc_with_reconnect(self, method: str, params: dict[str, object]) -> dict[str, object]:
        async with self._lock:
            self._require_connected()
            try:
                return await self._rpc_locked(method, params)
            except (McpProtocolError, OSError, ConnectionError):
                await self._close_locked()
                await self._connect_locked()
                await self._refresh_locked()
                return await self._rpc_locked(method, params)

    async def _rpc_locked(self, method: str, params: dict[str, object]) -> dict[str, object]:
        transport = self._require_connected()
        self._request_id += 1
        wire_params = dict(params)
        if self.config.protocol_version >= PROTOCOL_VERSION:
            wire_params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": self.config.protocol_version,
                "io.modelcontextprotocol/clientInfo": {"name": "kairo-kernel", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        response = await transport.request(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": wire_params}
        )
        error = response.get("error")
        if isinstance(error, dict):
            raise McpError(str(error.get("message") or f"MCP request failed: {method}"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(f"MCP {method} result must be an object.")
        if result.get("resultType") == "input_required":
            raise McpError("MCP sampling and elicitation requests are disabled.")
        return result

    def _require_connected(self) -> McpTransport:
        if self._transport is None:
            raise McpProtocolError(f"MCP server '{self.config.name}' is not connected.")
        return self._transport

    async def _close_locked(self) -> None:
        transport = self._transport
        self._transport = None
        self.server_capabilities = {}
        if transport is not None:
            await transport.close()

    def _local_name(self, qualified: str, namespace: str) -> str:
        return self._entry(qualified, getattr(self.catalog, namespace)).local_name

    @staticmethod
    def _entry(qualified: str, entries: tuple[CatalogEntry, ...]) -> CatalogEntry:
        for entry in entries:
            if entry.qualified_name == qualified:
                return entry
        raise McpProtocolError(f"Unknown MCP catalog name: {qualified}")


class McpHub:
    """Own multiple trusted clients while preserving server namespaces."""

    def __init__(self, clients: tuple[McpClient, ...]):
        self.clients = clients

    async def connect_all(self) -> tuple[McpCatalog, ...]:
        return tuple(await asyncio.gather(*(client.connect() for client in self.clients)))

    async def refresh_all(self) -> tuple[McpCatalog, ...]:
        return tuple(await asyncio.gather(*(client.refresh() for client in self.clients)))

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self.clients))

    def catalog(self) -> tuple[CatalogEntry, ...]:
        entries = tuple(entry for client in self.clients for entry in client.catalog.all_entries())
        names = [entry.qualified_name for entry in entries]
        if len(names) != len(set(names)):
            raise McpProtocolError("MCP hub contains duplicate qualified names.")
        return tuple(sorted(entries, key=lambda item: item.qualified_name))


def _default_transport(config: McpServerConfig) -> McpTransport:
    if config.transport == "stdio":
        return StdioTransport(config)
    if config.transport == "http":
        return StreamableHttpTransport(config)
    raise ValueError(f"Unsupported MCP transport: {config.transport}")
