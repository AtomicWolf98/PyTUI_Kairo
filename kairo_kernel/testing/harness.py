"""Reusable deterministic conformance fixtures for embedded kernel hosts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kairo_kernel import KairoKernel, KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import (
    ErrorCode,
    EventType,
    InteractionAction,
    MessageKind,
    MessageRole,
    ProviderStreamKind,
    TurnStatus,
)
from kairo_kernel.contracts.events import KernelEvent, TurnEvent
from kairo_kernel.contracts.identifiers import InteractionId, MessageId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionResponse
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.contracts.support import SessionRecord, SessionSummary
from kairo_kernel.contracts.tools import ToolDescriptor
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.tools import ToolPort

PROFILE = ProviderProfile(
    ProfileId("conformance/model"),
    "Conformance",
    "conformance",
    "model",
    "https://invalid.example",
    32_000,
    1_000,
    0.0,
)


class ScriptedProvider:
    """Provider whose streams are held until the harness releases a round."""

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self._gate = asyncio.Event()

    def hold(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        del profile_id, role
        return KernelResult.success(PROFILE)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        del profile_id
        return KernelResult.success(PROFILE)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        self.requests.append(request)
        return self._stream(cancellation)

    async def _stream(self, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        gate_task = asyncio.create_task(self._gate.wait())
        cancellation_task = asyncio.create_task(cancellation.wait())
        done, pending = await asyncio.wait((gate_task, cancellation_task), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if cancellation_task in done:
            return
        yield ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock("ok"),))
        yield ProviderStreamEvent(ProviderStreamKind.COMPLETED)


class InMemorySessions:
    """Atomic repository fake shared by stress and fault tests."""

    def __init__(self, records: Iterable[SessionRecord]) -> None:
        self._records = {record.session_id: record for record in records}
        self._lock = asyncio.Lock()
        self.fail_save = False

    async def list(self) -> tuple[SessionSummary, ...]:
        async with self._lock:
            return tuple(
                SessionSummary(item.session_id, item.name, len(item.messages), item.created_at, item.updated_at)
                for item in self._records.values()
            )

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        async with self._lock:
            record = self._records.get(session_id)
        if record is None:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "Session was not found."))
        return KernelResult.success(record)

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        del active
        if self.fail_save:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_PERSISTENCE_FAILED, "Injected save fault."))
        async with self._lock:
            self._records[session.session_id] = session
        return KernelResult.success(session)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        async with self._lock:
            removed = self._records.pop(session_id, None)
        if removed is None:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "Session was not found."))
        return KernelResult.success(True)


class EmptyTools:
    async def list(self) -> tuple[ToolDescriptor, ...]:
        return ()

    async def get(self, name: str) -> KernelResult[ToolPort]:
        return KernelResult.failure(KernelError(ErrorCode.TOOL_NOT_FOUND, f"Tool not found: {name}"))

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        return KernelResult.success(())


@dataclass(frozen=True)
class StressReport:
    submitted: int
    cancelled: int
    busy_mutations: int
    invalid_responses: int
    terminal_counts: tuple[tuple[TurnId, int], ...]


class ConformanceHarness:
    def __init__(self, kernel: KairoKernel, provider: ScriptedProvider, sessions: tuple[SessionId, ...]) -> None:
        self.kernel = kernel
        self.provider = provider
        self.session_ids = sessions

    @classmethod
    def create(cls, workspace: str | Path, *, session_count: int = 5) -> ConformanceHarness:
        root = Path(workspace)
        now = datetime.now(timezone.utc)
        records = tuple(
            SessionRecord(
                SessionId(f"session-{index}"),
                f"Session {index}",
                (
                    Message(
                        MessageId(f"system-{index}"),
                        MessageRole.SYSTEM,
                        MessageKind.CHAT,
                        (TextBlock("system"),),
                    ),
                ),
                now,
                now,
            )
            for index in range(session_count)
        )
        repository = InMemorySessions(records)
        provider = ScriptedProvider()
        config = KernelConfig(
            str(root),
            database_path=str(root / "conformance.db"),
            event_buffer_size=20_000,
            enable_builtin_tools=False,
        )
        kernel = build_kernel(
            config,
            KernelDependencies(provider=provider, tools=EmptyTools(), sessions=repository),
        )
        return cls(kernel, provider, tuple(record.session_id for record in records))

    async def run(self, *, rounds: int = 10) -> StressReport:
        submitted = cancelled = busy = invalid = 0
        turn_ids: list[TurnId] = []
        for round_index in range(rounds):
            self.provider.hold()
            accepted = await asyncio.gather(
                *(self.kernel.submit(TurnRequest(f"round {round_index}", session_id)) for session_id in self.session_ids)
            )
            values = []
            for result in accepted:
                assert result.value is not None
                values.append(result.value)
                turn_ids.append(result.value.turn_id)
            submitted += len(values)

            for index, value in enumerate(values):
                mutation = await self.kernel.sessions.rename(value.session_id, f"busy {round_index}-{index}")
                if mutation.error is not None and mutation.error.code is ErrorCode.KERNEL_BUSY:
                    busy += 1
                response = await self.kernel.interactions.respond(
                    InteractionResponse(
                        InteractionId(f"missing-{round_index}-{index}"),
                        value.turn_id,
                        InteractionAction.REJECT,
                    )
                )
                if response.error is not None and response.error.code is ErrorCode.INTERACTION_NOT_FOUND:
                    invalid += 1
                if (round_index + index) % 4 == 0:
                    receipt = await self.kernel.cancel(value.turn_id, "stress cancellation")
                    if receipt.value is not None and receipt.value.requested:
                        cancelled += 1

            self.provider.release()
            completed = await asyncio.gather(*(self.kernel.wait(value.turn_id, 2) for value in values))
            assert all(result.value is not None and result.value.status in _TERMINAL for result in completed)
            for index, value in enumerate(values):
                renamed = await self.kernel.sessions.rename(value.session_id, f"round {round_index}-{index}")
                assert renamed.ok

        replay = await self.kernel.events.snapshot()
        counts = terminal_event_counts(replay.events, turn_ids)
        return StressReport(submitted, cancelled, busy, invalid, tuple(sorted(counts.items(), key=lambda item: item[0])))


def terminal_event_counts(events: tuple[KernelEvent, ...], turn_ids: Iterable[TurnId]) -> dict[TurnId, int]:
    counts = dict.fromkeys(turn_ids, 0)
    for event in events:
        if event.event_type is not EventType.TURN or event.turn_id not in counts:
            continue
        if isinstance(event.payload, TurnEvent) and event.payload.status in _TERMINAL:
            assert event.session_id is not None
            counts[event.turn_id] += 1
    return counts


def secret_leaks(values: Iterable[object], secrets: Iterable[str]) -> tuple[str, ...]:
    rendered = "\n".join(_render(value) for value in values)
    return tuple(secret for secret in secrets if secret and secret in rendered)


def _render(value: object) -> str:
    serializer = getattr(value, "to_json", None)
    if callable(serializer):
        try:
            result = serializer()
        except (TypeError, ValueError):
            pass
        else:
            if isinstance(result, str):
                return result
    return repr(value)


_TERMINAL = {TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED}
