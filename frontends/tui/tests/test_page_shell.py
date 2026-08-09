"""Page shell: per-page screens, nav buttons, keyboard shortcuts, no duplicate mounts.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_app_layout.py and
test_chat_screen.py, because pytest-asyncio's auto-mode loop rejects the nested
``asyncio.run`` inside ``build_running_kernel``. ``page_app_factory`` seeds a
complete config document so the app boots onto the Chat page.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import PageId
from tests.support.fakes import NOW_PROFILE, FakeProvider


@pytest.fixture
def page_app_factory(workspace: Path):
    """A booted KairoTuiApp on the Chat page with a seeded config (setup complete)."""

    def make(*, size: tuple[int, int] = (140, 40)) -> KairoTuiApp:
        document = ConfigDocument(
            profiles=(NOW_PROFILE,),
            roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
            default_profile_id=NOW_PROFILE.profile_id,
        )
        ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
            provider=FakeProvider(),
        )
        return KairoTuiApp(bootstrap)
    return make


def test_default_page_is_chat_when_setup_complete(page_app_factory) -> None:
    app = page_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.store.state.setup_complete is True
            assert app.store.state.page is PageId.CHAT
            assert app.query_one("#chat-screen") is not None

    asyncio.run(drive())


def test_nav_click_mounts_settings_screen(page_app_factory) -> None:
    app = page_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#nav-settings")
            await pilot.pause()
            assert app.query_one("#settings-screen") is not None
            assert app.store.state.page is PageId.SETTINGS

    asyncio.run(drive())


def test_shortcuts_mount_sessions_and_doctor(page_app_factory) -> None:
    app = page_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+2")
            await pilot.pause()
            assert app.query_one("#sessions-screen") is not None
            await pilot.press("ctrl+7")
            await pilot.pause()
            assert app.query_one("#doctor-screen") is not None

    asyncio.run(drive())


def test_navigate_away_and_back_mounts_single_instance(page_app_factory) -> None:
    """Round-trip away and back; each screen exists exactly once (no duplicate ids)."""
    app = page_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert len(app.query("#chat-screen")) == 1
            await pilot.press("ctrl+3")
            await pilot.pause()
            assert len(app.query("#workspace-screen")) == 1
            assert len(app.query("#chat-screen")) == 0
            await pilot.press("ctrl+4")
            await pilot.pause()
            assert len(app.query("#memory-screen")) == 1
            assert len(app.query("#workspace-screen")) == 0
            await pilot.press("ctrl+3")
            await pilot.pause()
            assert len(app.query("#workspace-screen")) == 1
            assert len(app.query("#memory-screen")) == 0
            await pilot.press("ctrl+1")
            await pilot.pause()
            assert len(app.query("#chat-screen")) == 1

    asyncio.run(drive())


def test_seven_nav_buttons_render(page_app_factory) -> None:
    app = page_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            nav_buttons = app.query("#nav Button")
            assert len(nav_buttons) == 7
            for page in (
                PageId.CHAT, PageId.SESSIONS, PageId.WORKSPACE, PageId.MEMORY,
                PageId.EXTENSIONS, PageId.SETTINGS, PageId.DOCTOR,
            ):
                button = app.query_one(f"#nav-{page.value}", Button)
                assert button is not None

    asyncio.run(drive())
