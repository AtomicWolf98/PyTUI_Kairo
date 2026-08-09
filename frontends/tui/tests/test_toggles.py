"""Preference toggles, new-chat, and the command palette Esc close.

Checks the Task-10 self-review items the brief's tests do not cover: toggles
persist through ``kernel.preferences.patch`` and reflect in the top bar, the
safe-mode authorization guard, Ctrl+N new chat, and Esc closing the palette
(priority chain branch 1). Same sync-bootstrap + ``asyncio.run(drive())``
pattern as test_commands.py.
"""

from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import AuthorizationMode

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import PageId
from kairo_tui.widgets import TopBar


def test_toggle_thinking_persists_and_reflects_in_top_bar(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            snapshot = await app.kernel.preferences.snapshot()
            assert snapshot.thinking_mode is False
            assert "think-off" in str(app.query_one("#topbar", TopBar).content)

    asyncio.run(drive())


def test_toggle_authorization_flips_mode(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            snapshot = await app.kernel.preferences.snapshot()
            assert snapshot.authorization_mode is AuthorizationMode.AUTO

    asyncio.run(drive())


def test_toggle_authorization_noop_in_safe_mode(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(
            workspace_root=str(workspace),
            config_path=workspace.parent / "config-v1.json",
            safe_mode=True,
        ),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+a")
            await pilot.pause()
            snapshot = await app.kernel.preferences.snapshot()
            assert snapshot.authorization_mode is AuthorizationMode.MANUAL

    asyncio.run(drive())


def test_new_chat_creates_session_and_opens_chat(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert app.store.state.active_session_id is not None
            assert app.store.state.page is PageId.CHAT

    asyncio.run(drive())


def test_esc_closes_command_palette(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


def test_reduced_motion_bootstrap_sets_class(workspace) -> None:
    """A `--reduced-motion` bootstrap sets the `bp-reduced-motion` class on the
    app (the CSS gate that disables animations)."""
    bootstrap = build_running_kernel(
        BootstrapOptions(
            workspace_root=str(workspace),
            config_path=workspace.parent / "config-v1.json",
            reduced_motion=True,
        ),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.has_class("bp-reduced-motion")

    asyncio.run(drive())


def test_ctrl_b_navigates_workspace(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.store.state.page is PageId.WORKSPACE

    asyncio.run(drive())
