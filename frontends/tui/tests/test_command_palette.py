"""Command palette: the merged TUI + kernel registry as a modal list.

The palette is the same registry as the slash input (tui_plan.md): TUI entries
render first, kernel business commands follow with their catalog help text.
Selecting a kernel command runs ``kernel.commands.execute`` and dismisses.
"""

from __future__ import annotations

import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Button

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore


def _app(workspace) -> KairoTuiApp:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    return KairoTuiApp(bootstrap)


async def _wait_for(pilot, predicate, *, polls: int = 40, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


def test_palette_lists_kernel_command(workspace) -> None:
    """The palette merges the kernel catalog: /status appears with its help text."""
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_command_palette()
            await pilot.pause()
            # The palette is a pushed modal: App.query_one only sees the base
            # screen, so query the active screen.
            status_button = app.screen.query_one("#cmd-status", Button)
            assert "Show read-only kernel status" in str(status_button.label)
            app.screen.query_one("#cmd-settings", Button)  # TUI nav entries stay listed

    asyncio.run(drive())


def test_selecting_kernel_command_in_palette_executes(workspace) -> None:
    """Selecting /status runs the kernel command and dismisses the palette."""
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_command_palette()
            await pilot.pause()
            # Kernel entries can overflow the visible screen region, so press the
            # button directly (Button.press posts Button.Pressed like a click).
            app.screen.query_one("#cmd-status", Button).press()
            await _wait_for(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1  # dismissed after execution

    asyncio.run(drive())


def test_palette_is_scrollable_when_overflowing(workspace) -> None:
    """At 140x40 the 21-entry palette overflows its 80%-height box and scrolls."""
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_command_palette()
            await pilot.pause()
            palette = app.screen.query_one("#palette", VerticalScroll)
            await pilot.pause()
            assert palette.max_scroll_y >= 1  # the 21 entries overflow

    asyncio.run(drive())


def test_palette_scroll_reaches_last_kernel_command(workspace) -> None:
    """Scroll_end reaches the last kernel command, which still executes."""
    app = _app(workspace)

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.action_command_palette()
            await pilot.pause()
            palette = app.screen.query_one("#palette", VerticalScroll)
            # scroll_end is sync in Textual 8.x; animate=False keeps the test
            # deterministic (the 1s animation would not finish in one pause()).
            palette.scroll_end(animate=False)
            await pilot.pause()
            assert palette.scroll_y == palette.max_scroll_y
            app.screen.query_one("#cmd-status", Button).press()
            await _wait_for(pilot, lambda: len(app.screen_stack) == 1)
            assert len(app.screen_stack) == 1  # executes and dismisses

    asyncio.run(drive())
