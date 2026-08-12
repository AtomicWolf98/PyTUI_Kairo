"""Regression tests for the chat-first shell introduced in the TUI redesign."""

from __future__ import annotations

import asyncio

from textual.widgets import Input

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.management import ManagementModal
from kairo_tui.store import PageId
from kairo_tui.widgets import Composer, StatusLine, TopBar


def _app(workspace) -> KairoTuiApp:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    return KairoTuiApp(bootstrap)


def test_default_shell_has_no_visual_navigation_rail(workspace) -> None:
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#nav").region.width <= 1
            assert app.query_one("#inspector").styles.opacity == 0
            assert app.query_one("#topbar", TopBar).content is not None
            assert app.query_one("#status-line", StatusLine) is not None
            assert app.query_one("#composer", Composer) is not None

    asyncio.run(drive())


def test_command_palette_is_searchable(workspace) -> None:
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.action_command_palette()
            await pilot.pause()
            assert isinstance(app.screen, object)
            assert app.screen.query_one("#command-search", Input) is not None
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


def test_leader_sidebar_toggle_is_overlay_only(workspace) -> None:
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+x")
            await pilot.press("b")
            await pilot.pause()
            assert app.query_one("#inspector").has_class("drawer-open")
            assert app.store.state.page is PageId.SETUP

    asyncio.run(drive())


def test_compact_terminal_keeps_chat_surface(workspace) -> None:
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            assert app.query_one("#page").display is True
            assert app.query_one("#composer").display is True

    asyncio.run(drive())


def test_management_command_uses_modal_shell(workspace) -> None:
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.open_management(PageId.SETTINGS)
            await pilot.pause()
            assert isinstance(app.screen, ManagementModal)
            assert app.store.state.page is PageId.SETTINGS
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())
