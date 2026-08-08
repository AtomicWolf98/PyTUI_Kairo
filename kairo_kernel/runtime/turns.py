"""Per-session turn admission and lifetime supervision."""

from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SessionId, TurnId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.errors import KernelError, KernelResult


class TurnLease:
    def __init__(self, supervisor: SessionTurnSupervisor, session_id: SessionId, turn_id: TurnId) -> None:
        self._supervisor = supervisor
        self.session_id = session_id
        self.turn_id = turn_id
        self._released = False

    async def __aenter__(self) -> TurnLease:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.release()

    async def release(self) -> bool:
        if self._released:
            return False
        self._released = True
        return await self._supervisor.finish(self.session_id, self.turn_id)


class SessionTurnSupervisor:
    """Allow one active turn per session while permitting other sessions."""

    def __init__(self) -> None:
        self._active: dict[SessionId, TurnId] = {}
        self._condition = asyncio.Condition()
        self._closing = False

    async def start(self, session_id: SessionId, turn_id: TurnId) -> KernelResult[TurnLease]:
        async with self._condition:
            if self._closing:
                return KernelResult.failure(KernelError(ErrorCode.KERNEL_CLOSING, "Turn supervisor is closing."))
            active = self._active.get(session_id)
            if active is not None:
                return KernelResult.failure(
                    KernelError(
                        ErrorCode.KERNEL_BUSY,
                        f"Session already has active turn {active}.",
                        retryable=True,
                        details=JsonObject.from_pairs(("active_turn_id", str(active))),
                    )
                )
            if turn_id in self._active.values():
                return KernelResult.failure(KernelError(ErrorCode.CONFLICT, "Turn id is already active."))
            self._active[session_id] = turn_id
            return KernelResult.success(TurnLease(self, session_id, turn_id))

    async def finish(self, session_id: SessionId, turn_id: TurnId) -> bool:
        async with self._condition:
            if self._active.get(session_id) != turn_id:
                return False
            del self._active[session_id]
            self._condition.notify_all()
            return True

    async def active(self) -> tuple[tuple[SessionId, TurnId], ...]:
        async with self._condition:
            return tuple(self._active.items())

    async def close_admission(self) -> tuple[tuple[SessionId, TurnId], ...]:
        async with self._condition:
            self._closing = True
            return tuple(self._active.items())

    async def wait_idle(self, timeout_seconds: float | None = None) -> bool:
        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: not self._active)

        try:
            if timeout_seconds is None:
                await wait()
            else:
                await asyncio.wait_for(wait(), timeout_seconds)
            return True
        except TimeoutError:
            return False
