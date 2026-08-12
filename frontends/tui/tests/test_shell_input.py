"""P0 acceptance: real keyboard paths into the V2 shell.

No handler is called directly; every assertion goes through Pilot key
presses or Textual paste events, at real terminal sizes.
"""

from __future__ import annotations

from textual import events

from kairo_tui.app import KairoTuiApp
from kairo_tui.widgets.composer import Composer


async def _paste(app: KairoTuiApp, text: str) -> None:
    """Deliver a real Textual paste event; the App forwards it to the composer."""
    app.post_message(events.Paste(text))


async def test_composer_has_focus_after_mount() -> None:
    app = KairoTuiApp()
    async with app.run_test():
        composer = app.query_one("#composer", Composer)
        assert app.focused is composer


async def test_typing_ascii_updates_draft() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        composer = app.query_one("#composer", Composer)
        assert composer.text == "hello"
        assert app.state.draft == "hello"


async def test_typing_chinese_updates_draft() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        await _paste(app, "你好 🚀")
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        assert composer.text == "你好 🚀"
        assert app.state.draft == "你好 🚀"


async def test_paste_multiline_preserves_text() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        await _paste(app, "first line\nsecond line")
        await pilot.pause()
        composer = app.query_one("#composer", Composer)
        assert composer.text == "first line\nsecond line"
        assert app.state.draft == "first line\nsecond line"


async def test_enter_posts_submit_intent_without_clearing_draft() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        composer = app.query_one("#composer", Composer)
        assert app._last_submitted == "hi"
        assert composer.text == "hi"
        assert app.state.draft == "hi"


async def test_shift_enter_inserts_newline() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        await pilot.press("a", "b")
        await pilot.press("shift+enter")
        await pilot.press("c")
        composer = app.query_one("#composer", Composer)
        assert composer.text == "ab\nc"


async def test_ctrl_l_restores_composer_focus() -> None:
    app = KairoTuiApp()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.blur()
        await pilot.pause()
        assert app.focused is not composer
        await pilot.press("ctrl+l")
        assert app.focused is composer


async def test_composer_is_enabled_without_provider() -> None:
    app = KairoTuiApp()  # no kernel, no provider catalog
    async with app.run_test():
        composer = app.query_one("#composer", Composer)
        assert composer.disabled is False
        assert composer.can_focus is True


async def test_80x24_keeps_composer_visible() -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(80, 24)) as pilot:
        composer = app.query_one("#composer", Composer)
        await pilot.pause()
        assert composer.display
        assert composer.region.height >= 3
        assert composer.region.bottom <= app.size.height


async def test_60x20_keeps_composer_focusable() -> None:
    app = KairoTuiApp()
    async with app.run_test(size=(60, 20)) as pilot:
        composer = app.query_one("#composer", Composer)
        await pilot.pause()
        assert composer.display
        composer.focus()
        await pilot.pause()
        assert app.focused is composer
