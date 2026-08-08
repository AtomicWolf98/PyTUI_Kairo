"""Small async HTTP boundary used by provider adapters.

The adapters depend on this protocol rather than a concrete HTTP library.  Tests
provide deterministic in-memory streams; the stdlib implementation keeps the
runtime dependency-free and moves blocking urllib work off the event loop.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout_seconds: float = 60.0


class HttpStream(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> tuple[tuple[str, str], ...]: ...

    def iter_bytes(self) -> AsyncIterator[bytes]: ...

    async def read(self) -> bytes: ...

    async def close(self) -> None: ...


class AsyncHttpTransport(Protocol):
    async def open(self, request: HttpRequest) -> HttpStream: ...


class UrllibAsyncHttpTransport:
    """Dependency-free transport suitable for normal CLI use."""

    async def open(self, request: HttpRequest) -> HttpStream:
        response = await asyncio.to_thread(self._open, request)
        return _UrllibStream(response)

    @staticmethod
    def _open(request: HttpRequest) -> _ReadableResponse:
        raw = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method="POST",
        )
        response: _ReadableResponse
        try:
            response = urllib.request.urlopen(raw, timeout=request.timeout_seconds)  # noqa: S310
        except urllib.error.HTTPError as error:
            response = error
        return response


class _ReadableResponse(Protocol):
    @property
    def status(self) -> int | None: ...

    @property
    def code(self) -> int | None: ...

    @property
    def headers(self) -> object: ...

    def read(self) -> bytes: ...

    def readline(self) -> bytes: ...

    def close(self) -> None: ...


class _UrllibStream:
    def __init__(self, response: _ReadableResponse):
        self._response = response

    @property
    def status_code(self) -> int:
        status = self._response.status
        if status is None:
            status = self._response.code
        return status or 0

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        headers = getattr(self._response, "headers", None)
        if headers is None:
            return ()
        return tuple((str(key), str(value)) for key, value in headers.items())

    async def read(self) -> bytes:
        value = await asyncio.to_thread(self._response.read)
        return bytes(value)

    async def close(self) -> None:
        await asyncio.to_thread(self._response.close)

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        readline = self._response.readline
        while True:
            line = await asyncio.to_thread(readline)
            if not line:
                return
            yield bytes(line)


async def iter_sse(stream: HttpStream) -> AsyncIterator[tuple[str, str]]:
    """Parse SSE frames even when transport chunks split arbitrary boundaries."""

    buffer = ""
    async for chunk in stream.iter_bytes():
        buffer += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            parsed = _parse_frame(frame)
            if parsed is not None:
                yield parsed
    if buffer.strip():
        parsed = _parse_frame(buffer)
        if parsed is not None:
            yield parsed


def _parse_frame(frame: str) -> tuple[str, str] | None:
    event = "message"
    data: list[str] = []
    for line in frame.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if not data:
        return None
    return event, "\n".join(data)
