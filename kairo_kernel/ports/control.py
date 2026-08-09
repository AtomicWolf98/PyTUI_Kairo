"""Lifecycle, cancellation and event-stream ports."""

from __future__ import annotations

from typing import Protocol

from kairo_kernel.contracts.events import EventReplay, KernelEvent
from kairo_kernel.contracts.identifiers import TurnId
from kairo_kernel.contracts.lifecycle import (
    KernelCapabilities,
    KernelStatus,
    LifecycleState,
    ShutdownReport,
    ShutdownRequest,
)
from kairo_kernel.contracts.turns import CancelReceipt, TurnAccepted, TurnRequest, TurnResult, TurnSnapshot
from kairo_kernel.errors import KernelResult


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    async def wait(self) -> None: ...


class EventSubscription(Protocol):
    async def receive(self) -> KernelEvent: ...

    async def close(self) -> None: ...


class EventPort(Protocol):
    async def snapshot(self, after_sequence: int = 0, limit: int = 1000) -> EventReplay: ...

    async def subscribe(self, after_sequence: int = 0) -> EventSubscription: ...

    async def publish(self, event: KernelEvent) -> None: ...


class TurnPort(Protocol):
    async def submit(self, request: TurnRequest) -> KernelResult[TurnAccepted]: ...

    async def get(self, turn_id: TurnId) -> KernelResult[TurnSnapshot]: ...

    async def wait(self, turn_id: TurnId, timeout_seconds: float | None = None) -> KernelResult[TurnResult]: ...

    async def cancel(self, turn_id: TurnId, reason: str = "") -> KernelResult[CancelReceipt]: ...


class KernelLifecyclePort(Protocol):
    async def start(self) -> KernelResult[LifecycleState]: ...

    async def status(self) -> KernelStatus: ...

    async def capabilities(self) -> KernelCapabilities: ...

    async def shutdown(self, request: ShutdownRequest) -> KernelResult[ShutdownReport]: ...

