from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from kairo_kernel.mcp import McpClient, McpServerConfig, McpServerTrustStore


class FakeTransport:
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        method = message["method"]
        request_id = message["id"]
        if method == "server/discover":
            result: dict[str, object] = {
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "offline"}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}]}
        else:
            result = {}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def notify(self, message: dict[str, object]) -> None:
        del message

    async def close(self) -> None:
        return None


async def main() -> None:
    with TemporaryDirectory(prefix="kairo-mcp-") as directory:
        config = McpServerConfig("demo", "stdio", command="unused")
        trust = McpServerTrustStore(Path(directory) / "mcp-trust.json")
        trust.trust(config, config.digest)
        client = McpClient(config, trust, transport_factory=lambda _: FakeTransport())
        catalog = await client.connect()
        tool = catalog.tools[0]
        assert tool.qualified_name == "mcp__demo__tools__echo"
        result = await client.call_tool(tool.qualified_name, {"text": "hello"})
        assert "content" in result
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
