"""MCP JSON-RPC transports for stdio and Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from kairo_kernel.mcp.models import McpProtocolError, McpServerConfig


class McpTransport(Protocol):
    async def request(self, message: dict[str, object]) -> dict[str, object]: ...

    async def notify(self, message: dict[str, object]) -> None: ...

    async def close(self) -> None: ...


class StdioTransport:
    def __init__(self, config: McpServerConfig):
        if config.transport != "stdio" or not config.command:
            raise ValueError("Stdio transport requires a command.")
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._process is not None:
            return
        environment = _allowed_environment(self.config)
        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.arguments,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
        )

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        async with self._lock:
            await self.start()
            await self._write(message)
            request_id = message.get("id")
            while True:
                incoming = await self._read()
                if incoming.get("id") == request_id and ("result" in incoming or "error" in incoming):
                    return incoming
                if "id" in incoming and isinstance(incoming.get("method"), str):
                    await self._write(_rejection(incoming))

    async def notify(self, message: dict[str, object]) -> None:
        async with self._lock:
            await self.start()
            await self._write(message)

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
            await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            process.terminate()
            await process.wait()

    async def _write(self, message: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise McpProtocolError("MCP stdio process has no stdin.")
        process.stdin.write(_json_bytes(message) + b"\n")
        await process.stdin.drain()

    async def _read(self) -> dict[str, object]:
        process = self._process
        if process is None or process.stdout is None:
            raise McpProtocolError("MCP stdio process has no stdout.")
        line = await process.stdout.readline()
        if not line:
            raise McpProtocolError("MCP stdio server closed the stream.")
        return _json_object(line)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class HttpSender(Protocol):
    async def send(
        self,
        method: str,
        url: str,
        headers: tuple[tuple[str, str], ...],
        body: bytes,
    ) -> HttpResponse: ...


class UrllibHttpSender:
    async def send(
        self,
        method: str,
        url: str,
        headers: tuple[tuple[str, str], ...],
        body: bytes,
    ) -> HttpResponse:
        return await asyncio.to_thread(self._send, method, url, headers, body)

    @staticmethod
    def _send(method: str, url: str, headers: tuple[tuple[str, str], ...], body: bytes) -> HttpResponse:
        request = urllib.request.Request(url, data=body or None, headers=dict(headers), method=method)
        try:
            response = urllib.request.urlopen(request)  # noqa: S310
            status = int(response.status)
            response_headers = tuple((str(key), str(value)) for key, value in response.headers.items())
            content = response.read()
            response.close()
        except urllib.error.HTTPError as error:
            status = error.code
            response_headers = tuple((str(key), str(value)) for key, value in error.headers.items())
            content = error.read()
            error.close()
        return HttpResponse(status, response_headers, bytes(content))


class StreamableHttpTransport:
    def __init__(self, config: McpServerConfig, sender: HttpSender | None = None):
        if config.transport != "http" or not config.url:
            raise ValueError("Streamable HTTP transport requires a URL.")
        self.config = config
        self.sender = sender or UrllibHttpSender()
        self.session_id = ""

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        response = await self.sender.send("POST", self.config.url, self._headers(message), _json_bytes(message))
        self._capture_session(response.headers)
        if response.status_code < 200 or response.status_code >= 300:
            raise McpProtocolError(f"MCP HTTP request failed with status {response.status_code}.")
        messages = _http_messages(response)
        request_id = message.get("id")
        for incoming in messages:
            if incoming.get("id") == request_id and ("result" in incoming or "error" in incoming):
                return incoming
            if "id" in incoming and isinstance(incoming.get("method"), str):
                await self.notify(_rejection(incoming))
        raise McpProtocolError("MCP HTTP response did not contain the requested JSON-RPC result.")

    async def notify(self, message: dict[str, object]) -> None:
        response = await self.sender.send("POST", self.config.url, self._headers(message), _json_bytes(message))
        self._capture_session(response.headers)
        if response.status_code not in (200, 202, 204):
            raise McpProtocolError(f"MCP HTTP notification failed with status {response.status_code}.")

    async def close(self) -> None:
        if self.config.protocol_version >= "2026-07-28" or not self.session_id:
            return
        response = await self.sender.send("DELETE", self.config.url, self._headers({}), b"")
        if response.status_code not in (200, 202, 204, 404, 405):
            raise McpProtocolError(f"MCP HTTP session close failed with status {response.status_code}.")
        self.session_id = ""

    def _headers(self, message: dict[str, object]) -> tuple[tuple[str, str], ...]:
        headers = (
            ("content-type", "application/json"),
            ("accept", "application/json, text/event-stream"),
        ) + self.config.headers
        if self.config.protocol_version >= "2026-07-28":
            method = message.get("method")
            if isinstance(method, str):
                headers += (("mcp-protocol-version", self.config.protocol_version), ("mcp-method", method))
            params = message.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if isinstance(name, str):
                headers += (("mcp-name", name),)
        elif self.session_id:
            headers += (("mcp-session-id", self.session_id),)
        return headers

    def _capture_session(self, headers: tuple[tuple[str, str], ...]) -> None:
        if self.config.protocol_version >= "2026-07-28":
            return
        for key, value in headers:
            if key.lower() == "mcp-session-id":
                self.session_id = value


def _allowed_environment(config: McpServerConfig) -> dict[str, str]:
    allowed = set(config.environment_allowlist)
    configured = dict(config.environment)
    denied = sorted(set(configured) - allowed)
    if denied:
        raise McpProtocolError(f"MCP environment variables are not allowlisted: {', '.join(denied)}")
    output = {name: value for name, value in os.environ.items() if name in allowed}
    output.update(configured)
    return output


def _http_messages(response: HttpResponse) -> tuple[dict[str, object], ...]:
    content_type = next((value.lower() for key, value in response.headers if key.lower() == "content-type"), "")
    if "text/event-stream" not in content_type:
        return (_json_object(response.body),)
    messages: list[dict[str, object]] = []
    buffer = response.body.decode("utf-8", errors="replace").replace("\r\n", "\n")
    for frame in buffer.split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
        if data:
            messages.append(_json_object(data.encode()))
    return tuple(messages)


def _rejection(request: dict[str, object]) -> dict[str, object]:
    method = str(request.get("method") or "")
    message = "Server-initiated sampling and elicitation are disabled."
    code = -32601
    if method in {"sampling/createMessage", "elicitation/create"}:
        code = -32000
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {"code": code, "message": message if code == -32000 else "Method not supported."},
    }


def _json_bytes(message: dict[str, object]) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()


def _json_object(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise McpProtocolError(f"Invalid MCP JSON: {error}") from error
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise McpProtocolError("MCP message must be a JSON-RPC 2.0 object.")
    return value
