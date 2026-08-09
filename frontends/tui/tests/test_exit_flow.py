"""Exit flow with background turns: wait / stop-all / back.

Sync-bootstrap + ``asyncio.run(drive())`` pattern (as test_commands.py and the
Task-10 exit tests): ``build_running_kernel`` calls ``asyncio.run`` internally,
which raises inside pytest-asyncio's event loop (asyncio_mode = "auto"). The
brief's original ``async def`` tests are therefore wrapped in ``drive()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from kairo_kernel.contracts.enums import ProviderStreamKind, TurnStatus
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.exit_modal import ExitWithTurnsModal
from kairo_tui.store import SessionAction
from tests.support.fakes import FakeProvider


@pytest.fixture
def app_with_provider(workspace: Path):
    def make(*, delay: float = 0.0, block: bool = False) -> KairoTuiApp:
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),), delay=delay, block=block)
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
            provider=provider,
        )
        return KairoTuiApp(bootstrap)
    return make


async def _submit_blocking_turn(app: KairoTuiApp) -> None:
    created = await app.kernel.sessions.create("Chat")
    assert created.value is not None
    await app.kernel.submit(TurnRequest("work", session_id=created.value.session_id))


async def _wait_for(pilot, predicate, *, polls: int = 100, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return
    raise AssertionError("condition not reached in time")


def test_exit_without_turns_exits_immediately(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.run_worker(app.request_exit())
            await pilot.pause()
            assert app.kernel.state.value in ("stopping", "stopped")

    asyncio.run(drive())


def test_exit_with_active_turn_shows_three_options(app_with_provider) -> None:
    app = app_with_provider(block=True)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_blocking_turn(app)
            await pilot.pause()
            assert app.store.state.active_turns != ()
            app.run_worker(app.request_exit())
            await pilot.pause()
            assert isinstance(app.screen, ExitWithTurnsModal)

    asyncio.run(drive())


def test_exit_stop_all_cancels_and_exits(app_with_provider) -> None:
    app = app_with_provider(block=True)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_blocking_turn(app)
            await pilot.pause()
            app.run_worker(app.request_exit())
            await pilot.pause()
            await pilot.click("#exit-stop")
            await _wait_for(pilot, lambda: app.kernel.state.value == "stopped")
            report = app.kernel.state
            assert report.value == "stopped"

    asyncio.run(drive())


def test_exit_wait_completes_after_turn_finishes(app_with_provider) -> None:
    app = app_with_provider(delay=0.2)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_blocking_turn(app)
            await pilot.pause()
            app.run_worker(app.request_exit())
            await pilot.pause()
            assert isinstance(app.screen, ExitWithTurnsModal)
            await pilot.click("#exit-wait")
            await pilot.pause()
            await _wait_for(pilot, lambda: app.kernel.state.value == "stopped")
            assert app.kernel.state.value == "stopped"

    asyncio.run(drive())


def test_exit_back_keeps_app_running(app_with_provider) -> None:
    app = app_with_provider(block=True)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_blocking_turn(app)
            await pilot.pause()
            app.run_worker(app.request_exit())
            await pilot.pause()
            await pilot.click("#exit-back")
            await _wait_for(pilot, lambda: not isinstance(app.screen, ExitWithTurnsModal))
            assert app.kernel.state.value == "running"
            assert not isinstance(app.screen, ExitWithTurnsModal)

    asyncio.run(drive())


def test_esc_closes_modal_before_cancelling(app_with_provider) -> None:
    """Esc priority chain: a pushed modal is closed first (no turn cancelled)."""
    app = app_with_provider(block=True)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ExitWithTurnsModal(1))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            # Textual keeps the base screen on the stack; only the modal pops.
            assert not isinstance(app.screen, ExitWithTurnsModal)
            assert app.store.state.active_turns == ()  # no turn was touched

    asyncio.run(drive())


def test_esc_cancels_foreground_turn_only(app_with_provider) -> None:
    """Esc cancels the active session's turn; background sessions' turns survive."""
    app = app_with_provider(block=True)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            session_a = await app.kernel.sessions.create("A")
            assert session_a.value is not None
            session_b = await app.kernel.sessions.create("B")
            assert session_b.value is not None
            await app.kernel.submit(TurnRequest("a", session_id=session_a.value.session_id))
            await app.kernel.submit(TurnRequest("b", session_id=session_b.value.session_id))
            app.store.dispatch(SessionAction(str(session_a.value.session_id)))
            await pilot.pause()
            await _wait_for(pilot, lambda: len(app.store.state.active_turns) == 2)
            assert len(app.store.state.active_turns) == 2
            turn_a = next(
                t for t in app.store.state.active_turns
                if str(t.session_id) == str(session_a.value.session_id)
            )
            turn_b = next(
                t for t in app.store.state.active_turns
                if str(t.session_id) == str(session_b.value.session_id)
            )
            await pilot.press("escape")
            await _wait_for(pilot, lambda: app.store.state.turn_status.get(str(turn_a.turn_id)) == TurnStatus.CANCELLED.value)
            assert app.store.state.turn_status.get(str(turn_a.turn_id)) == TurnStatus.CANCELLED.value
            assert app.store.state.turn_status.get(str(turn_b.turn_id)) == TurnStatus.RUNNING.value
            assert turn_b.turn_id in {t.turn_id for t in app.store.state.active_turns}

    asyncio.run(drive())
