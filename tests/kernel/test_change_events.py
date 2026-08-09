from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ErrorCode, EventType, ProviderStreamKind
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.identifiers import MemoryId, ProfileId, SecretId, SessionId
from kairo_kernel.contracts.providers import ProviderProfile, ProviderStreamEvent
from kairo_kernel.contracts.support import MemoryEntry, SecretInput
from kairo_kernel.services import ConfigChange, ConfigPatch
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, FakeTools, session

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        default_session_id=SessionId("session-1"),
        enable_builtin_tools=False,
        trust_directory=str(Path(root).parent / f"{Path(root).name}-trust"),
    )


def _provider() -> FakeProvider:
    return FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))


def _kernel(tmp_path: object):
    return build_kernel(
        _config(tmp_path),
        KernelDependencies(provider=_provider(), tools=FakeTools(), sessions=FakeSessions(session())),
    )


def test_session_and_conversation_mutations_emit_session_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            created = await kernel.sessions.create("Notes")
            assert created.ok and created.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SESSION_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1
            assert event.payload.subject_id == str(created.value.session_id)

            renamed = await kernel.sessions.rename(created.value.session_id, "Renamed")
            assert renamed.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SESSION_CHANGED
            assert event.payload.revision == 2

            cleared = await kernel.conversations.clear(SessionId("session-1"))
            assert cleared.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SESSION_CHANGED
            assert event.payload.subject_id == "session-1"
            await subscription.close()

    asyncio.run(exercise())


def test_memory_mutations_emit_memory_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            entry = MemoryEntry(MemoryId("m-1"), "user", "key", (TextBlock("hello"),), NOW, NOW)
            saved = await kernel.memory.put(entry)
            assert saved.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.MEMORY_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1
            assert event.payload.subject_id == "m-1"

            deleted = await kernel.memory.delete(MemoryId("m-1"))
            assert deleted.ok and deleted.value is True
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.MEMORY_CHANGED
            assert event.payload.revision == 2
            await subscription.close()

    asyncio.run(exercise())


def test_failed_mutations_emit_nothing(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            before = (await kernel.events.snapshot()).newest_sequence
            missing = await kernel.sessions.rename(SessionId("missing"), "Nope")
            assert missing.error is not None
            deleted = await kernel.memory.delete(MemoryId("absent"))
            assert deleted.error is not None and deleted.error.code is ErrorCode.NOT_FOUND
            after = (await kernel.events.snapshot()).newest_sequence
            assert after == before

    asyncio.run(exercise())


def test_config_patch_emits_config_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            patched = await kernel.configuration.patch(ConfigPatch(0, (ConfigChange(("ui", "theme"), "dark"),)))
            assert patched.ok and patched.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.CONFIG_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1
            await subscription.close()

    asyncio.run(exercise())


def test_workspace_move_emits_workspace_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        target = Path(str(tmp_path)) / "other"
        target.mkdir()
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            state = await kernel.workspace.snapshot()
            moved = await kernel.workspace.move(str(target), state.revision)
            assert moved.ok and moved.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.WORKSPACE_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == moved.value.revision
            assert event.payload.subject_id == moved.value.root
            await subscription.close()

    asyncio.run(exercise())


def test_provider_mutations_emit_provider_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        profile = ProviderProfile(
            ProfileId("openai/gpt-test"),
            "OpenAI",
            "openai_chat",
            "gpt-test",
            "https://api.example.test/v1",
            128_000,
            4_096,
            0.2,
        )
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            created = await kernel.providers.create_profile(profile, 0)
            assert created.ok and created.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.PROVIDER_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1

            stored = await kernel.providers.store_secret(SecretInput(SecretId("openai-key"), "sk-test"))
            assert stored.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.PROVIDER_CHANGED
            assert event.payload.subject_id == "openai-key"
            await subscription.close()

    asyncio.run(exercise())


def test_skill_mutations_emit_skills_changed(tmp_path: object) -> None:
    async def exercise() -> None:
        skills_dir = Path(str(tmp_path)) / ".kairo" / "skills"
        skills_dir.mkdir(parents=True)
        kernel = _kernel(tmp_path)
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            reloaded = await kernel.skills.reload()
            assert reloaded.ok and reloaded.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SKILLS_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1

            trusted = await kernel.skills.trust(reloaded.value.digest)
            assert trusted.ok and trusted.value is not None
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SKILLS_CHANGED
            assert event.payload.revision == 2

            revoked = await kernel.skills.revoke()
            assert revoked.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.SKILLS_CHANGED
            assert event.payload.revision == 3
            await subscription.close()

    asyncio.run(exercise())
