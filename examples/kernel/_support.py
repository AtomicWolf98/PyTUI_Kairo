from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TypeVar

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ProviderStreamKind
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.control import CancellationToken

PROFILE = ProviderProfile(
    ProfileId("example/echo"),
    "Offline echo",
    "example",
    "echo",
    "https://example.invalid/v1",
    32_000,
    2_000,
    0.0,
)
ResultT = TypeVar("ResultT")


class EchoProvider:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        del profile_id, role
        return KernelResult.success(PROFILE)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        del profile_id
        return KernelResult.success(PROFILE)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        return self._stream(request, cancellation)

    async def _stream(
        self,
        request: ProviderRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ProviderStreamEvent]:
        if self.delay:
            await asyncio.sleep(self.delay)
        if cancellation.cancelled:
            return
        text = next(
            (
                block.text
                for message in reversed(request.messages)
                for block in message.content
                if isinstance(block, TextBlock)
            ),
            "",
        )
        yield ProviderStreamEvent(ProviderStreamKind.CONTENT, content=(TextBlock(f"echo: {text}"),))
        yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason="stop")


def value(result: KernelResult[ResultT]) -> ResultT:
    if result.error is not None:
        raise RuntimeError(f"{result.error.code.value}: {result.error.message}")
    assert result.value is not None
    return result.value
