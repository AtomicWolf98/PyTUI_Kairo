"""Deliver kernel events to the AppStore; recover from replay gaps or overflow.

The only kernel→UI data path. Incremental rendering is paused while a recovery
re-reads authoritative state (status, sessions, active turns, workspace, pending
interactions) and resubscribes from the newest sequence.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Protocol, cast

from kairo_kernel.contracts.content import Message
from kairo_kernel.contracts.events import EventReplay, KernelEvent
from kairo_kernel.contracts.interactions import InteractionRequest
from kairo_kernel.contracts.lifecycle import KernelStatus
from kairo_kernel.contracts.support import SessionSummary
from kairo_kernel.contracts.turns import ActiveTurn
from kairo_kernel.errors import KernelResult

from kairo_tui.chat_model import history_messages
from kairo_tui.store import AppStore, ChatMessage, EventAction, RecoveryAction

# How long the pump waits for the next event before re-checking its stop flag.
_RECEIVE_TIMEOUT = 0.02


class EventSubscription(Protocol):
    async def receive(self) -> KernelEvent: ...
    async def close(self) -> None: ...


class EventSource(Protocol):
    async def snapshot(self, after_sequence: int = 0, limit: int = 1000) -> EventReplay: ...
    async def subscribe(self, after_sequence: int = 0, queue_size: int | None = None) -> EventSubscription: ...


class _SessionsSurface(Protocol):
    async def list(self) -> KernelResult[tuple[SessionSummary, ...]]: ...


class _ConversationsSurface(Protocol):
    async def history(self, session_id) -> KernelResult[tuple[Message, ...]]: ...


class _InteractionsSurface(Protocol):
    async def pending(self) -> tuple[InteractionRequest, ...]: ...


class _WorkspaceSurface(Protocol):
    async def snapshot(self) -> object: ...


class _KernelSurface(Protocol):
    """Structural kernel surface the pump touches; the recovery reads are
    best-effort at runtime (each is wrapped in try/except in _recover)."""

    events: EventSource
    sessions: _SessionsSurface
    conversations: _ConversationsSurface
    interactions: _InteractionsSurface
    workspace: _WorkspaceSurface

    async def status(self) -> KernelStatus: ...
    async def active_turns(self) -> tuple[ActiveTurn, ...]: ...


class ReplayGap(RuntimeError):
    def __init__(self, after_sequence: int) -> None:
        self.after_sequence = after_sequence
        super().__init__(f"Event buffer gap after sequence {after_sequence}.")


def is_subscriber_overflow(exc: BaseException) -> bool:
    """Match by class name or by the overflow message.

    SubscriberOverflow is a RuntimeError in a private kernel module the TUI may
    not import (AST boundary), so we match by name; the message fallback covers
    test doubles that raise a plain RuntimeError with the same message.
    """
    if type(exc).__name__ == "SubscriberOverflow":
        return True
    text = str(exc)
    return "Subscriber lost" in text and "event(s) after sequence" in text


class EventPump:
    def __init__(self, kernel: object, store: AppStore, *, queue_size: int | None = None) -> None:
        self._kernel = cast(_KernelSurface, kernel)
        self._store = store
        self._queue_size = queue_size
        self._stop = asyncio.Event()
        self._subscription: EventSubscription | None = None

    async def run(self) -> None:
        """Subscribe from the store's last sequence and fold events."""
        while not self._stop.is_set():
            after = self._store.state.last_event_sequence
            replay = await self._kernel.events.snapshot(after_sequence=after)
            if replay.gap:
                if self._stop.is_set():
                    break
                await self._recover(sequence=replay.newest_sequence)
                continue
            subscription = await self._kernel.events.subscribe(after_sequence=after, queue_size=self._queue_size)
            self._subscription = subscription
            try:
                while not self._stop.is_set():
                    try:
                        event = await asyncio.wait_for(subscription.receive(), timeout=_RECEIVE_TIMEOUT)
                    except TimeoutError:
                        continue  # idle poll; re-check stop state
                    except RuntimeError as exc:
                        if is_subscriber_overflow(exc):
                            break  # → recovery
                        if self._stop.is_set() or "closed" in str(exc):
                            return  # clean stop (pump.close or kernel shutdown)
                        raise
                    self._store.dispatch(EventAction(event))
                if self._stop.is_set():
                    break
                await self._recover()
            finally:
                await subscription.close()
                self._subscription = None

    async def close(self) -> None:
        """Stop the loop; wakes a blocked receive() via subscription.close()."""
        self._stop.set()
        subscription = self._subscription
        if subscription is not None:
            await subscription.close()

    async def _recover(self, sequence: int | None = None) -> None:
        """Pause incremental rendering; re-read authoritative state; resubscribe.

        sequence, when given, advances the store's last event sequence so the
        next snapshot/subscribe resumes past a replay gap instead of re-hitting it.
        """
        try:
            status = await self._kernel.status()
        except Exception:
            status = None
        sessions: tuple = ()
        try:
            result = await self._kernel.sessions.list()
            sessions = result.value or ()
        except Exception:
            pass
        active: tuple = ()
        with suppress(Exception):
            active = await self._kernel.active_turns()
        pending: tuple = ()
        with suppress(Exception):
            pending = await self._kernel.interactions.pending()
        root, revision = "", 0
        try:
            snapshot = await self._kernel.workspace.snapshot()
            root = str(getattr(snapshot, "root", "") or "")
            revision = int(getattr(snapshot, "revision", 0) or 0)
        except Exception:
            pass
        # Recovery re-reads every session's committed history so the store's
        # timeline is authoritative even when deltas were lost to the gap.
        recovered_messages: list[ChatMessage] = []
        for summary in sessions:
            with suppress(Exception):
                history_result = await self._kernel.conversations.history(summary.session_id)
                if history_result.ok and history_result.value:
                    recovered_messages.extend(history_messages(history_result.value, str(summary.session_id)))
        self._store.dispatch(
            RecoveryAction(
                status=status,
                sessions=sessions,
                turns=active,
                pending=pending,
                workspace_root=root,
                workspace_revision=revision,
                last_event_sequence=sequence,
                messages=tuple(recovered_messages),
            )
        )
