from __future__ import annotations

import asyncio

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import AuthorizationMode, EventType, ProviderStreamKind
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.identifiers import ProfileId, SessionId
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.contracts.providers import ProviderProfile, ProviderStreamEvent
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, FakeTools, session

PROFILE = ProviderProfile(ProfileId("openai/gpt-test"), "GPT", "openai_chat", "gpt-test", "https://x.test/v1", 32000, 1000, 0.2)


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        default_session_id=SessionId("session-1"),
        default_profile_id=PROFILE.profile_id,
        profiles=(PROFILE,),
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


def test_preferences_snapshot_patch_and_change_event(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            snapshot = await kernel.preferences.snapshot()
            assert snapshot.revision == 0
            assert snapshot.authorization_mode is AuthorizationMode.MANUAL
            assert snapshot.thinking_mode is True

            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            patched = await kernel.preferences.patch(
                PreferencesPatch(0, authorization_mode=AuthorizationMode.AUTO, thinking_mode=False)
            )
            assert patched.ok and patched.value is not None
            assert patched.value.revision == 1
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.CONFIG_CHANGED
            assert isinstance(event.payload, ChangeEvent)
            assert event.payload.revision == 1
            assert event.payload.subject_id == "preferences"
            await subscription.close()

    asyncio.run(exercise())


def test_status_reflects_preferences_and_real_context(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        async with kernel:
            status = await kernel.status()
            assert status.authorization_mode is AuthorizationMode.MANUAL
            assert status.active_profile_id == PROFILE.profile_id
            assert status.context.context_window == 32000
            assert status.context.used_tokens > 0
            assert 0.0 < status.context.percent < 100.0

            patched = await kernel.preferences.patch(PreferencesPatch(0, plan_mode=True))
            assert patched.ok
            status = await kernel.status()
            assert status.plan_mode is True

    asyncio.run(exercise())


def test_preferences_patch_is_mutation_gated(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path)
        early = await kernel.preferences.patch(PreferencesPatch(0, plan_mode=True))
        assert early.error is not None and early.error.code.value == "kernel_not_running"

    asyncio.run(exercise())
