"""Kernel event consumption: atomic replay -> live, with recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

from kairo_kernel import KairoKernel
from kairo_kernel.runtime import SubscriberOverflow

from kairo_tui_v2.reducer import NoticeSet, UiAction, fold_event, reduce
from kairo_tui_v2.state import AppState


class KernelEventLoop:
    """Consume kernel events and fold them into the immutable AppState.

    Contract (C0):
    - subscribe with the current last sequence; the kernel's atomic
      replay -> live semantics are authoritative;
    - duplicate sequences are dropped; a sequence gap triggers recovery;
    - subscriber overflow re-subscribes from the last folded sequence;
    - handler failures never kill the loop: a redacted notice is emitted
      and the loop retries once;
    - close() cancels the worker and leaves no task behind.
    """

    def __init__(
        self,
        kernel: KairoKernel,
        initial_state: AppState,
        *,
        emit: Callable[[AppState, tuple[UiAction, ...]], None],
        recover: Callable[[], None] | None = None,
    ) -> None:
        self._kernel = kernel
        self._state = initial_state
        self._emit = emit
        self._recover = recover or (lambda: None)
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def state(self) -> AppState:
        return self._state

    def sync_state(self, state: AppState) -> None:
        """Adopt externally-applied state so folds never lose app actions."""
        self._state = state

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closed = False
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        sequence = self._state.last_sequence
        retried = False
        while not self._closed:
            try:
                subscription = await self._kernel.events.subscribe(after_sequence=sequence)
                retried = False
                while not self._closed:
                    event = await subscription.receive()
                    if event.sequence <= sequence:
                        continue
                    if event.sequence > sequence + 1:
                        self._notice("Event stream gap; recovering state.")
                        self._recover()
                        self._state = replace(self._state, last_sequence=event.sequence)
                        sequence = event.sequence
                        continue
                    try:
                        next_state, actions = fold_event(self._state, event)
                    except Exception:
                        if retried:
                            raise
                        retried = True
                        self._notice("Event processing failed; retrying once.")
                        continue
                    self._state = next_state
                    sequence = event.sequence
                    self._emit(self._state, actions)
            except SubscriberOverflow:
                self._notice("Event stream overflow; re-subscribing.")
                continue
            except asyncio.CancelledError:
                return
            except Exception:
                if self._closed:
                    return
                if retried:
                    raise
                retried = True
                self._notice("Event subscription failed; retrying once.")
                await asyncio.sleep(0.05)

    def _notice(self, text: str) -> None:
        action = NoticeSet(text)
        self._state = reduce(self._state, action)
        self._emit(self._state, (action,))
