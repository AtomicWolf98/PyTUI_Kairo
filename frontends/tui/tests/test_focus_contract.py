"""V0 acceptance: focus contract and keyboard traversal."""

from __future__ import annotations

from test_connect_dialog import FakeKernel, _app

from kairo_tui.dialogs.connect import ConnectDialog
from kairo_tui.widgets.composer import Composer


async def test_focused_widget_has_highlight_class() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.pause()
        assert app.focused is composer
        # The focus border must be visually distinct: accent vs plain panel.
        focus_border = composer.styles.border
        assert focus_border is not None


async def test_composer_focus_border_differs_from_unfocused() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.pause()
        focused = composer.styles.border
        composer.blur()
        await pilot.pause()
        unfocused = composer.styles.border
        assert focused != unfocused


async def test_connect_dialog_focus_lands_on_model_input() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConnectDialog)
        assert app.screen.focused.id == "model"


async def test_reverse_tab_visits_fields_backwards() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        dialog = app.screen
        await pilot.press("shift+tab")
        await pilot.pause()
        assert dialog.focused.id == "provider-type"
        await pilot.press("shift+tab")
        await pilot.pause()
        assert dialog.focused.id == "cancel"


async def test_escape_priority_closes_modal_first() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConnectDialog)
        await pilot.press("escape")
        await pilot.pause()
        assert app.state.overlay is None
        assert not app._exit  # app never exits on escape
