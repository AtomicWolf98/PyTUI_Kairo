from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode, ProviderStreamKind, TurnStatus
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, session


async def _wait_running(kernel, turn_id) -> None:
    for _ in range(200):
        snapshot = await kernel.turn(turn_id)
        if snapshot.value is not None and snapshot.value.status is TurnStatus.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("turn did not reach RUNNING")


def test_workspace_move_returns_busy_while_turn_active_then_succeeds(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = str(tmp_path)
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        provider.block = True
        kernel = build_kernel(
            KernelConfig(
                root,
                database_path=str(root + "/kernel.db"),
                default_session_id=SessionId("session-1"),
                enable_builtin_tools=False,
            ),
            KernelDependencies(provider=provider, sessions=FakeSessions(session())),
        )
        target = tmp_path / "next"
        target.mkdir()
        async with kernel:
            accepted = await kernel.submit(TurnRequest("work", SessionId("session-1")))
            assert accepted.value is not None
            await _wait_running(kernel, accepted.value.turn_id)

            state = await kernel.workspace.snapshot()
            busy = await kernel.workspace.move(str(target), state.revision)
            assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY
            assert busy.error.retryable
            after = await kernel.workspace.snapshot()
            assert after.root == state.root and after.revision == state.revision  # untouched

            assert (await kernel.cancel(accepted.value.turn_id)).ok
            assert (await kernel.wait(accepted.value.turn_id, 2)).ok
            moved = await kernel.workspace.move(str(target), state.revision)
            assert moved.ok and moved.value is not None
            assert moved.value.root == str(target.resolve())

    asyncio.run(exercise())


def test_workspace_command_returns_busy_while_turn_active(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = str(tmp_path)
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        provider.block = True
        kernel = build_kernel(
            KernelConfig(
                root,
                database_path=str(root + "/kernel.db"),
                default_session_id=SessionId("session-1"),
                enable_builtin_tools=False,
            ),
            KernelDependencies(provider=provider, sessions=FakeSessions(session())),
        )
        target = tmp_path / "next"
        target.mkdir()
        async with kernel:
            accepted = await kernel.submit(TurnRequest("work", SessionId("session-1")))
            assert accepted.value is not None
            await _wait_running(kernel, accepted.value.turn_id)

            parsed = kernel.commands.parse(f"/workspace {target}")
            assert parsed.ok and parsed.value is not None
            busy = await kernel.commands.execute(parsed.value, SessionId("session-1"))
            assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY

            assert (await kernel.cancel(accepted.value.turn_id)).ok
            assert (await kernel.wait(accepted.value.turn_id, 2)).ok
            retried = await kernel.commands.execute(
                kernel.commands.parse(f"/workspace {target}").value, SessionId("session-1")
            )
            assert retried.ok and retried.value is not None

    asyncio.run(exercise())
