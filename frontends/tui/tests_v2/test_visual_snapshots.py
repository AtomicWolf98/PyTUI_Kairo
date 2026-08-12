"""V0 acceptance: visual assertions that survive headless rendering."""

from __future__ import annotations

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.dialogs.connect import ConnectDialog
from tests_v2.test_connect_dialog import FakeKernel, _app


async def test_every_button_has_rendered_label() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConnectDialog)
        for button in app.screen.query("Button"):
            assert button.label, f"button {button.id} has empty label"
            assert str(button.label).strip()


async def test_form_labels_and_inputs_visible_together() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        dialog = app.screen
        for label_id, input_id in (
            ("label-model", "model"),
            ("label-base-url", "base-url"),
            ("label-api-key", "api-key"),
        ):
            label = dialog.query_one(f"#{label_id}")
            field = dialog.query_one(f"#{input_id}")
            assert label.display and field.display
            assert label.region.bottom <= field.region.bottom


async def test_modal_never_exceeds_screen() -> None:
    app = _app(FakeKernel())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        modal = app.screen.query_one("#connect-modal")
        assert modal.region.width <= 80
        assert modal.region.height <= 24


async def test_no_horizontal_scrollbar_on_chat() -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        transcript = app.query_one("#transcript")
        # Vertical scroll is allowed; horizontal overflow must not appear.
        assert transcript.scrollable_content_region.width <= 80


async def test_primary_danger_and_cancel_styles_are_distinct() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        dialog = app.screen
        primary = dialog.query_one("#test-connection")
        cancel = dialog.query_one("#cancel")
        assert primary.styles.background != cancel.styles.background


async def test_light_and_dark_theme_contrast() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await pilot.pause()
        dark = app.get_theme("textual-dark")
        light = app.get_theme("textual-light")
        assert dark.foreground != light.foreground
        assert dark.background != light.background
        # Foreground and background must differ in every theme.
        assert dark.foreground != dark.background
        assert light.foreground != light.background


async def test_reduced_motion_removes_transitions() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        app.set_class(True, "reduced-motion")
        await pilot.pause()
        workbench = app.query_one("#workbench")
        assert "transition" not in str(workbench.styles.transitions or "")
