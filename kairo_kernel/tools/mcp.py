"""Bridge MCP catalog tools into the kernel tool port."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from kairo_kernel.contracts.enums import ErrorCode, OperationScope, ToolExecutionStatus
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.tools import (
    ToolDescriptor,
    ToolExecutionContext,
    ToolInvocation,
    ToolResult,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import CatalogEntry, McpClient, McpError, McpHub
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolOutputSink, ToolPort


class McpTool:
    """Adapt one MCP catalog tool entry to the kernel tool port."""

    def __init__(self, client: McpClient, entry: CatalogEntry) -> None:
        self._client = client
        self._entry = entry
        self._descriptor = _descriptor(client, entry)

    @property
    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    async def classify(self, invocation: ToolInvocation) -> KernelResult[OperationScope]:
        del invocation
        return KernelResult.success(OperationScope.EXTERNAL)

    async def execute(
        self,
        invocation: ToolInvocation,
        context: ToolExecutionContext,
        cancellation: CancellationToken,
        output: ToolOutputSink,
    ) -> ToolResult:
        del context, cancellation, output
        started = datetime.now(timezone.utc)
        arguments = thaw_json(invocation.arguments)
        try:
            result = await self._client.call_tool(
                self._entry.qualified_name, arguments if isinstance(arguments, dict) else {}
            )
        except McpError as exc:
            return ToolResult(
                invocation.tool_call_id,
                invocation.name,
                ToolExecutionStatus.FAILED,
                (TextBlock(str(exc)),),
                started,
                datetime.now(timezone.utc),
                str(exc),
            )
        failed = result.get("isError") is True
        return ToolResult(
            invocation.tool_call_id,
            invocation.name,
            ToolExecutionStatus.FAILED if failed else ToolExecutionStatus.SUCCEEDED,
            _content_blocks(result),
            started,
            datetime.now(timezone.utc),
            "MCP tool reported an error." if failed else "",
        )


class McpToolRegistry:
    """Expose the current MCP hub catalog as a tool registry (dynamic, no caching)."""

    def __init__(self, hub: McpHub) -> None:
        self._hub = hub

    async def list(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            _descriptor(client, entry) for client in self._hub.clients for entry in client.catalog.tools
        )

    async def get(self, name: str) -> KernelResult[ToolPort]:
        for client in self._hub.clients:
            for entry in client.catalog.tools:
                if entry.qualified_name == name:
                    return KernelResult.success(McpTool(client, entry))
        return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}"))

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        return KernelResult.success(await self.list())


def _descriptor(client: McpClient, entry: CatalogEntry) -> ToolDescriptor:
    description = entry.raw.get("description")
    schema = entry.raw.get("inputSchema")
    frozen = freeze_json(schema) if isinstance(schema, dict) else JsonObject()
    if not isinstance(frozen, JsonObject):
        frozen = JsonObject()
    return ToolDescriptor(
        entry.qualified_name,
        description if isinstance(description, str) and description.strip() else entry.qualified_name,
        frozen,
        ("mcp",),
        source="mcp",
        manifest_digest=client.config.digest,
    )


def _content_blocks(result: dict[str, object]) -> tuple[ContentBlock, ...]:
    content = result.get("content")
    blocks: list[ContentBlock] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                blocks.append(TextBlock(item["text"]))
            else:
                blocks.append(TextBlock(json.dumps(item, ensure_ascii=False, sort_keys=True)))
    if not blocks:
        blocks.append(TextBlock(json.dumps(result, ensure_ascii=False, sort_keys=True)))
    return tuple(blocks)
