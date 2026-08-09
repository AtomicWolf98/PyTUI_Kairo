from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode, EventType, ProviderStreamKind
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.providers import ProviderStreamEvent
from tests.kernel.engine.fakes import PROFILE, FakeProvider, FakeSessions, FakeTools, session


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        default_session_id=SessionId("session-1"),
        enable_builtin_tools=False,
    )


def _kernel(tmp_path: object):
    return build_kernel(
        _config(tmp_path),
        KernelDependencies(
            provider=FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),)),
            tools=FakeTools(),
            sessions=FakeSessions(session()),
        ),
    )


def test_parse_and_execute_round_trip(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        assert kernel.commands.catalog()
        async with kernel:
            parsed = kernel.commands.parse("/new From Test")
            assert parsed.ok and parsed.value is not None
            outcome = await kernel.commands.execute(parsed.value)
            assert outcome.ok and outcome.value is not None and outcome.value.session_id is not None

            listed = await kernel.commands.execute(kernel.commands.parse("/sessions").value)
            assert listed.ok and listed.value is not None and "From Test" in listed.value.message

            status = await kernel.commands.execute(kernel.commands.parse("/status").value)
            assert status.ok and status.value is not None and "running" in status.value.message

    asyncio.run(exercise())


def test_mutating_commands_are_lifecycle_gated(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        parsed = kernel.commands.parse("/new Too Early")
        assert parsed.ok and parsed.value is not None
        early = await kernel.commands.execute(parsed.value)
        assert early.error is not None and early.error.code is ErrorCode.KERNEL_NOT_RUNNING

        assert (await kernel.start()).ok
        await kernel.mark_degraded("test")
        degraded = await kernel.commands.execute(kernel.commands.parse("/new Nope").value)
        assert degraded.error is not None and degraded.error.code is ErrorCode.KERNEL_DEGRADED
        readable = await kernel.commands.execute(kernel.commands.parse("/status").value)
        assert readable.ok
        await kernel.shutdown()

    asyncio.run(exercise())


def test_mutating_commands_emit_change_events(tmp_path: object) -> None:
    async def exercise() -> None:
        target = Path(str(tmp_path)) / "elsewhere"
        target.mkdir()
        kernel = _kernel(tmp_path)
        async with kernel:
            profile = await kernel.providers.create_profile(PROFILE, 0)
            assert profile.ok
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)

            created = await kernel.commands.execute(kernel.commands.parse("/new From Events").value)
            assert created.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SESSION_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert created.value is not None and event.payload.subject_id == str(created.value.session_id)

            cleared = await kernel.commands.execute(kernel.commands.parse("/clear").value, SessionId("session-1"))
            assert cleared.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SESSION_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.subject_id == "session-1"

            modeled = await kernel.commands.execute(kernel.commands.parse("/model provider/model").value)
            assert modeled.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.CONFIG_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.subject_id == "preferences"

            moved = await kernel.commands.execute(kernel.commands.parse(f"/workspace {target}").value)
            assert moved.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.WORKSPACE_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert moved.value is not None and event.payload.subject_id in moved.value.message
            await subscription.close()

    asyncio.run(exercise())


def test_read_only_commands_emit_nothing(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            before = (await kernel.events.snapshot()).newest_sequence
            session_id = SessionId("session-1")
            for text in ("/sessions", "/status", "/find notes", "/doctor", "/skills", "/mcp", "/memory default"):
                parsed = kernel.commands.parse(text)
                assert parsed.ok and parsed.value is not None
                outcome = await kernel.commands.execute(parsed.value, session_id)
                assert outcome.ok, text
            exported = await kernel.commands.execute(kernel.commands.parse("/export json").value, session_id)
            assert exported.ok
            after = (await kernel.events.snapshot()).newest_sequence
            assert after == before

    asyncio.run(exercise())
