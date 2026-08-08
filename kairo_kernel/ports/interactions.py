"""Pending human-interaction broker port."""

from __future__ import annotations

from typing import Protocol

from kairo_kernel.contracts.interactions import InteractionReceipt, InteractionRequest, InteractionResponse
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.control import CancellationToken


class InteractionPort(Protocol):
    async def request(self, request: InteractionRequest, cancellation: CancellationToken) -> InteractionResponse: ...

    async def respond(self, response: InteractionResponse) -> KernelResult[InteractionReceipt]: ...

