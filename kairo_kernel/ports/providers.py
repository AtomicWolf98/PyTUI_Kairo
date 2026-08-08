"""LLM provider ports."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.control import CancellationToken


class ProviderPort(Protocol):
    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]: ...

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]: ...

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]: ...

