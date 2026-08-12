"""Workbench layout and responsive breakpoints via Textual Pilot.

Deviation from the brief (ratified): each test bootstraps the app synchronously
(outside any event loop) and drives the Pilot inside ``asyncio.run`` — the same
pattern the smoke driver uses — because pytest-asyncio's auto-mode loop rejects
the nested ``asyncio.run`` inside ``build_running_kernel``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import DraftAction


@pytest.fixture
def app_factory(workspace: Path):
    def make(*, size: tuple[int, int] = (140, 40)) -> KairoTuiApp:
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
        )
        return KairoTuiApp(bootstrap)
    return make


def test_full_layout_three_columns(app_factory) -> None:
    app = app_factory(size=(200, 50))

    async def drive() -> None:
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == "full"
            assert app.query_one("#nav").display is True
            assert app.query_one("#inspector").display is True

    asyncio.run(drive())


def test_narrow_layout_hides_inspector(app_factory) -> None:
    app = app_factory(size=(120, 30))

    async def drive() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == "narrow"
            assert app.query_one("#inspector").display is False

    asyncio.run(drive())


def test_overlay_layout_single_page(app_factory) -> None:
    app = app_factory(size=(90, 30))

    async def drive() -> None:
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == "overlay"

    asyncio.run(drive())


def test_eighty_by_twenty_four_is_overlay(app_factory) -> None:
    app = app_factory(size=(80, 24))

    async def drive() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == "overlay"

    asyncio.run(drive())


def test_compat_layout_below_80x24(app_factory) -> None:
    app = app_factory(size=(60, 20))

    async def drive() -> None:
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == "compat"
            # Chat-first remains usable even below the legacy 80x24 threshold;
            # the compat class only removes padding and nonessential chrome.
            assert app.query_one("#page").display is True

    asyncio.run(drive())


def test_draft_survives_resize(app_factory) -> None:
    app = app_factory(size=(200, 50))

    async def drive() -> None:
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            app.store.dispatch(DraftAction("keep me"))
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            await pilot.resize_terminal(200, 50)
            await pilot.pause()
            assert app.store.state.draft == "keep me"

    asyncio.run(drive())


def test_top_bar_renders_kernel_status(app_factory) -> None:
    app = app_factory(size=(140, 40))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            text = app.query_one("#topbar").content
            assert "Kairo" in str(text)

    asyncio.run(drive())


def test_page_switch_preserves_shell_widgets(app_factory) -> None:
    """Switching pages must not disturb the #topbar or #inspector mounts."""
    app = app_factory(size=(140, 40))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+3")
            await pilot.pause()
            assert app.query_one("#topbar") is not None
            assert app.query_one("#inspector") is not None
            assert app.query_one("#inspector").display is True
            assert "Kairo" in str(app.query_one("#topbar").content)

    asyncio.run(drive())


def test_compat_hint_shown_once_not_accumulated(app_factory) -> None:
    """Resize storms inside COMPAT append the hint exactly once, then remove it."""
    app = app_factory(size=(140, 40))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            await pilot.resize_terminal(59, 19)
            await pilot.pause()
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            assert str(app.query_one("#topbar").content).count("compat mode") == 1
            await pilot.resize_terminal(140, 40)
            await pilot.pause()
            assert "compat mode" not in str(app.query_one("#topbar").content)

    asyncio.run(drive())
