"""Fail-closed broker for tool, plan and text interactions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from kairo_kernel.contracts.enums import ErrorCode, InteractionAction
from kairo_kernel.contracts.identifiers import InteractionId
from kairo_kernel.contracts.interactions import InteractionReceipt, InteractionRequest, InteractionResponse
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken


class InteractionBrokerError(RuntimeError):
    def __init__(self, error: KernelError) -> None:
        self.error = error
        super().__init__(error.message)


@dataclass
class _Pending:
    request: InteractionRequest
    future: asyncio.Future[InteractionResponse]


class InteractionBroker:
    """Correlate UI responses without ever defaulting to approval."""

    def __init__(self, terminal_retention: int = 1000) -> None:
        self._pending: dict[InteractionId, _Pending] = {}
        self._terminal: dict[InteractionId, str] = {}
        self._terminal_order: list[InteractionId] = []
        self._terminal_retention = max(1, terminal_retention)
        self._lock = asyncio.Lock()
        self._closed = False

    async def request(self, request: InteractionRequest, cancellation: CancellationToken) -> InteractionResponse:
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._closed:
                raise InteractionBrokerError(KernelError(ErrorCode.KERNEL_CLOSING, "Interaction broker is closed."))
            if request.interaction_id in self._pending or request.interaction_id in self._terminal:
                raise InteractionBrokerError(
                    KernelError(ErrorCode.CONFLICT, "Interaction id has already been used.", interaction_id=request.interaction_id)
                )
            future: asyncio.Future[InteractionResponse] = loop.create_future()
            self._pending[request.interaction_id] = _Pending(request, future)

        cancellation_task = asyncio.create_task(cancellation.wait())
        timeout_task = asyncio.create_task(self._wait_until(request.expires_at))
        try:
            done, _ = await asyncio.wait(
                (future, cancellation_task, timeout_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                return future.result()
            state = "cancelled" if cancellation_task in done else "expired"
            response = self._safe_response(request)
            async with self._lock:
                pending = self._pending.pop(request.interaction_id, None)
                if pending is not None and not pending.future.done():
                    pending.future.cancel()
                self._remember_terminal(request.interaction_id, state)
            return response
        finally:
            cancellation_task.cancel()
            timeout_task.cancel()
            async with self._lock:
                pending = self._pending.pop(request.interaction_id, None)
                if pending is not None:
                    if not pending.future.done():
                        pending.future.cancel()
                    self._remember_terminal(request.interaction_id, "resolved")

    async def respond(self, response: InteractionResponse) -> KernelResult[InteractionReceipt]:
        async with self._lock:
            if self._closed:
                return KernelResult.failure(
                    KernelError(ErrorCode.KERNEL_CLOSING, "Interaction broker is closed.", interaction_id=response.interaction_id)
                )
            pending = self._pending.get(response.interaction_id)
            if pending is None:
                state = self._terminal.get(response.interaction_id)
                code = ErrorCode.INTERACTION_EXPIRED if state == "expired" else ErrorCode.CONFLICT if state else ErrorCode.INTERACTION_NOT_FOUND
                return KernelResult.failure(
                    KernelError(code, f"Interaction is not pending ({state or 'unknown'}).", interaction_id=response.interaction_id)
                )
            request = pending.request
            if request.expires_at is not None:
                expires_at = request.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= datetime.now(timezone.utc):
                    self._pending.pop(response.interaction_id, None)
                    pending.future.set_result(self._safe_response(request))
                    self._remember_terminal(response.interaction_id, "expired")
                    return KernelResult.failure(
                        KernelError(
                            ErrorCode.INTERACTION_EXPIRED,
                            "Interaction has expired.",
                            interaction_id=response.interaction_id,
                        )
                    )
            if response.turn_id != request.turn_id:
                return KernelResult.failure(
                    KernelError(ErrorCode.INVALID_ARGUMENT, "Interaction turn_id does not match.", interaction_id=response.interaction_id)
                )
            allowed = {choice.action for choice in request.choices}
            if response.action not in allowed:
                return KernelResult.failure(
                    KernelError(ErrorCode.INVALID_ARGUMENT, "Interaction action was not offered.", interaction_id=response.interaction_id)
                )
            if response.action is InteractionAction.SUBMIT_TEXT and not response.text.strip():
                return KernelResult.failure(
                    KernelError(ErrorCode.INVALID_ARGUMENT, "Text response must not be empty.", interaction_id=response.interaction_id)
                )
            if pending.future.done():
                return KernelResult.failure(
                    KernelError(ErrorCode.CONFLICT, "Interaction was already resolved.", interaction_id=response.interaction_id)
                )
            pending.future.set_result(response)
            return KernelResult.success(InteractionReceipt(response.interaction_id, response.turn_id, True))

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            pending_items = tuple(self._pending.items())
            self._pending.clear()
            for interaction_id, pending in pending_items:
                if not pending.future.done():
                    pending.future.set_result(self._safe_response(pending.request))
                self._remember_terminal(interaction_id, "shutdown")

    async def pending(self) -> tuple[InteractionRequest, ...]:
        async with self._lock:
            return tuple(item.request for item in self._pending.values())

    @staticmethod
    async def _wait_until(expires_at: datetime | None) -> None:
        if expires_at is None:
            await asyncio.Future()
            return
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delay = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        await asyncio.sleep(delay)

    @staticmethod
    def _safe_response(request: InteractionRequest) -> InteractionResponse:
        action = request.safe_default
        if action in (InteractionAction.APPROVE_ONCE, InteractionAction.ENABLE_AUTO, InteractionAction.ENABLE_YOLO):
            action = InteractionAction.REJECT
        return InteractionResponse(request.interaction_id, request.turn_id, action)

    def _remember_terminal(self, interaction_id: InteractionId, state: str) -> None:
        if interaction_id not in self._terminal:
            self._terminal_order.append(interaction_id)
        self._terminal[interaction_id] = state
        while len(self._terminal_order) > self._terminal_retention:
            oldest = self._terminal_order.pop(0)
            self._terminal.pop(oldest, None)
