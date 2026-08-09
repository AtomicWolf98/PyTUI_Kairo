from __future__ import annotations

import asyncio

import kairo_kernel
from kairo_kernel import KERNEL_API_VERSION, KairoKernel, KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode, ProviderStreamKind, TurnStatus
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.services import ConfigField, ConfigSchema, ConfigValueKind
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, FakeTools, session


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        default_session_id=SessionId("session-1"),
        enable_builtin_tools=False,
    )


def _provider(*, block: bool = False) -> FakeProvider:
    provider = FakeProvider(
        (
            ProviderStreamEvent(ProviderStreamKind.CONTENT, content=()),
            ProviderStreamEvent(ProviderStreamKind.COMPLETED),
        )
    )
    provider.block = block
    return provider


def test_root_exports_are_stable() -> None:
    assert KERNEL_API_VERSION == "1.1"
    assert {"KairoKernel", "KernelConfig", "KernelDependencies", "build_kernel", "KERNEL_API_VERSION"} <= set(
        kairo_kernel.__all__
    )


def test_fake_dependencies_run_complete_turn_and_correlate_events(tmp_path: object) -> None:
    async def exercise() -> None:
        sessions = FakeSessions(session())
        dependencies = KernelDependencies(provider=_provider(), tools=FakeTools(), sessions=sessions)
        kernel = build_kernel(_config(tmp_path), dependencies)
        assert isinstance(kernel, KairoKernel)

        async with kernel:
            accepted = await kernel.submit(TurnRequest("hello", SessionId("session-1")))
            assert accepted.value is not None
            completed = await kernel.wait(accepted.value.turn_id, 2)
            assert completed.value is not None and completed.value.status is TurnStatus.SUCCEEDED
            replay = await kernel.events.snapshot()
            correlated = tuple(event for event in replay.events if event.turn_id == accepted.value.turn_id)
            assert correlated
            assert all(event.kernel_id == kernel.kernel_id for event in correlated)
            assert all(event.session_id == SessionId("session-1") for event in correlated)
            assert [event.sequence for event in replay.events] == list(range(1, len(replay.events) + 1))

        assert kernel.state.value == "stopped"

    asyncio.run(exercise())


def test_busy_degraded_and_closing_guards(tmp_path: object) -> None:
    async def exercise() -> None:
        sessions = FakeSessions(session())
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(provider=_provider(block=True), tools=FakeTools(), sessions=sessions),
        )
        before_start = await kernel.sessions.create("blocked")
        assert before_start.error is not None and before_start.error.code is ErrorCode.KERNEL_NOT_RUNNING
        assert (await kernel.start()).ok

        accepted = await kernel.submit(TurnRequest("wait", SessionId("session-1")))
        assert accepted.value is not None
        busy = await kernel.sessions.rename(SessionId("session-1"), "busy")
        assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY

        first_shutdown = await kernel.shutdown()
        assert first_shutdown.value is not None and first_shutdown.value.active_turn_cancelled
        second_shutdown = await kernel.shutdown()
        assert second_shutdown.value == first_shutdown.value
        closing = await kernel.submit(TurnRequest("late", SessionId("session-1")))
        assert closing.error is not None and closing.error.code is ErrorCode.KERNEL_CLOSING

        degraded = build_kernel(
            _config(str(tmp_path) + "/degraded"),
            KernelDependencies(provider=_provider(), tools=FakeTools(), sessions=FakeSessions(session())),
        )
        assert (await degraded.start()).ok
        await degraded.mark_degraded("test failure")
        mutation = await degraded.sessions.create("disabled")
        assert mutation.error is not None and mutation.error.code is ErrorCode.KERNEL_DEGRADED
        status = await degraded.status()
        assert status.degraded_reason == "test failure"
        await degraded.shutdown()

    asyncio.run(exercise())


def test_config_secrets_are_redacted_at_public_boundary(tmp_path: object) -> None:
    async def exercise() -> None:
        values = freeze_json({"api_key": "sk-super-secret-value", "normal": "visible"})
        assert isinstance(values, JsonObject)
        config = _config(tmp_path)
        config = KernelConfig(
            config.workspace_root,
            database_path=config.database_path,
            default_session_id=config.default_session_id,
            enable_builtin_tools=False,
            config_values=values,
            config_schema=ConfigSchema(
                (
                    ConfigField(("api_key",), ConfigValueKind.STRING, secret=True),
                    ConfigField(("normal",), ConfigValueKind.STRING),
                )
            ),
        )
        kernel = build_kernel(
            config,
            KernelDependencies(provider=_provider(), tools=FakeTools(), sessions=FakeSessions(session())),
        )
        async with kernel:
            snapshot = await kernel.configuration.snapshot()
            public = thaw_json(snapshot.values)
            assert isinstance(public, dict)
            assert public["api_key"] == "[REDACTED]"
            assert public["normal"] == "visible"
            assert "sk-super-secret-value" not in await kernel.configuration.export_json()

    asyncio.run(exercise())
