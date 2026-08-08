"""Serialized asynchronous kernel lifecycle state transitions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from kairo_kernel.contracts.enums import ErrorCode, LifecycleState
from kairo_kernel.errors import KernelError, KernelResult

LifecycleHook = Callable[[], Awaitable[None]]


async def _noop() -> None:
    return None


class AsyncLifecycle:
    """Idempotent lifecycle with explicit degraded and timeout outcomes."""

    def __init__(self, start_hook: LifecycleHook = _noop, shutdown_hook: LifecycleHook = _noop) -> None:
        self._state = LifecycleState.CREATED
        self._reason = ""
        self._start_hook = start_hook
        self._shutdown_hook = shutdown_hook
        self._lock = asyncio.Lock()

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def degraded_reason(self) -> str:
        return self._reason

    async def start(self) -> KernelResult[LifecycleState]:
        async with self._lock:
            if self._state is LifecycleState.RUNNING:
                return KernelResult.success(self._state)
            if self._state is not LifecycleState.CREATED:
                return KernelResult.failure(
                    KernelError(ErrorCode.CONFLICT, f"Cannot start lifecycle from {self._state.value}.")
                )
            self._state = LifecycleState.STARTING
            try:
                await self._start_hook()
            except Exception as exc:
                self._state = LifecycleState.DEGRADED
                self._reason = str(exc)
                return KernelResult.failure(KernelError(ErrorCode.INTERNAL, str(exc), operation="start"))
            self._state = LifecycleState.RUNNING
            return KernelResult.success(self._state)

    async def mark_degraded(self, reason: str) -> None:
        async with self._lock:
            if self._state is not LifecycleState.STOPPED:
                self._state = LifecycleState.DEGRADED
                self._reason = reason

    async def shutdown(self, timeout_seconds: float = 5.0) -> KernelResult[LifecycleState]:
        async with self._lock:
            if self._state is LifecycleState.STOPPED:
                return KernelResult.success(self._state)
            if self._state is LifecycleState.STOPPING:
                return KernelResult.failure(KernelError(ErrorCode.CONFLICT, "Shutdown is already in progress."))
            self._state = LifecycleState.STOPPING
            try:
                await asyncio.wait_for(self._shutdown_hook(), max(0.0, timeout_seconds))
            except TimeoutError:
                self._state = LifecycleState.DEGRADED
                self._reason = "Lifecycle shutdown timed out."
                return KernelResult.failure(
                    KernelError(ErrorCode.SHUTDOWN_TIMEOUT, self._reason, operation="shutdown")
                )
            except Exception as exc:
                self._state = LifecycleState.DEGRADED
                self._reason = str(exc)
                return KernelResult.failure(KernelError(ErrorCode.INTERNAL, str(exc), operation="shutdown"))
            self._state = LifecycleState.STOPPED
            return KernelResult.success(self._state)
