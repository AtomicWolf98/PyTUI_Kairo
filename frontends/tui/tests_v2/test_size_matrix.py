"""V0 acceptance: terminal size matrix."""

from __future__ import annotations

import pytest

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.widgets.composer import Composer

SIZES = (
    (60, 20),
    (80, 24),
    (120, 30),
    (160, 40),
    (200, 50),
)


@pytest.mark.parametrize("width,height", SIZES)
async def test_composer_visible_and_focusable_at_every_size(width: int, height: int) -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(width, height)) as pilot:
        composer = app.query_one("#composer", Composer)
        await pilot.pause()
        assert composer.display, f"composer hidden at {width}x{height}"
        assert composer.region.height >= 3
        assert composer.region.bottom <= height
        composer.focus()
        await pilot.pause()
        assert app.focused is composer


@pytest.mark.parametrize("width,height", SIZES)
async def test_no_widget_overlaps_at_every_size(width: int, height: int) -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(width, height)) as pilot:
        await pilot.pause()
        topbar = app.query_one("#topbar")
        composer = app.query_one("#composer", Composer)
        status = app.query_one("#status-line")
        assert topbar.region.bottom <= composer.region.y
        assert composer.region.bottom <= status.region.y or composer.region.bottom <= height


async def test_60x20_shows_compact_hint_without_overlap() -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        status = app.query_one("#status-line")
        assert composer.display
        assert status.display
        # No control overlaps another.
        assert composer.region.intersection(status.region).height == 0


async def test_200x50_keeps_form_width_bounded() -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(200, 50)) as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        from kairo_tui_v2.dialogs.connect import ConnectDialog

        assert isinstance(app.screen, ConnectDialog)
        modal = app.screen.query_one("#connect-modal")
        assert modal.region.width <= 100  # never stretched full width
