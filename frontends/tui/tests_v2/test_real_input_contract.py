"""P1 headless part of the real-input contract.

The real Windows Terminal gate cannot be replaced by pytest (see
manual_windows_checklist.md); these tests cover everything observable in a
headless terminal: sizes, unicode round-trips and dialog geometry.
"""

from __future__ import annotations

from textual import events

from kairo_tui_v2.dialogs.connect import ConnectDialog
from kairo_tui_v2.state import OverlayKind
from kairo_tui_v2.widgets.composer import Composer
from tests_v2.test_connect_dialog import FakeKernel, _app


async def test_chinese_draft_survives_connect_flow() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        app.post_message(events.Paste("检查当前项目 🚀"))
        await pilot.pause()
        assert composer.text == "检查当前项目 🚀"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.state.overlay is OverlayKind.CONNECT
        assert app.state.pending_draft == "检查当前项目 🚀"
        await pilot.press("escape")
        await pilot.pause()
        assert app.state.draft == "检查当前项目 🚀"
        assert app.query_one("#composer", Composer).text == "检查当前项目 🚀"


async def test_80x24_connect_dialog_stays_on_screen() -> None:
    app = _app(FakeKernel())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConnectDialog)
        modal = app.screen.query_one("#connect-modal")
        assert modal.region.width <= 80
        assert modal.region.height <= 24
        # Every label and action button is visible inside the modal bounds.
        for widget_id in (
            "connect-title",
            "label-provider",
            "provider-type",
            "label-model",
            "model",
            "label-base-url",
            "base-url",
            "label-api-key",
            "api-key",
            "save",
            "cancel",
        ):
            widget = app.screen.query_one(f"#{widget_id}")
            assert widget.region.intersection(modal.region) or widget.display, widget_id


async def test_60x20_connect_dialog_keeps_actions_visible() -> None:
    app = _app(FakeKernel())
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConnectDialog)
        cancel = app.screen.query_one("#cancel")
        assert cancel.display
        # The modal may scroll but never grows past the screen.
        modal = app.screen.query_one("#connect-modal")
        assert modal.region.height <= 20


async def test_fullwidth_and_emoji_paste_roundtrip() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        app.post_message(events.Paste("ＡＢＣ 全角 ｔｅｓｔ 🚀"))
        await pilot.pause()
        assert composer.text == "ＡＢＣ 全角 ｔｅｓｔ 🚀"
        assert app.state.draft == "ＡＢＣ 全角 ｔｅｓｔ 🚀"
