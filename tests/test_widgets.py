import importlib.util
import unittest
from unittest.mock import patch

if importlib.util.find_spec("textual") is None:
    raise unittest.SkipTest("textual is not installed in the current test environment")

from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widgets import Static

from agent.tui_widgets import (
    format_dock_line,
    get_string_width,
    is_wide_char,
    select_menu,
    truncate_to_width,
)
from agent.ui.widgets import Composer, MessageBody


class _MessageBodyHarness(App[None]):
    CSS = """
    .message { overflow-x: auto; }
    """

    def __init__(self, content: str = "", is_markdown: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._content = content
        self._is_markdown = is_markdown

    def compose(self) -> ComposeResult:
        yield MessageBody(self._content, is_markdown=self._is_markdown, classes="message")

    @property
    def body(self) -> MessageBody:
        return self.query_one(MessageBody)


class TestTUIWidgets(unittest.TestCase):
    def test_is_wide_char(self):
        # ASCII should return False
        self.assertFalse(is_wide_char('a'))
        self.assertFalse(is_wide_char('/'))
        self.assertFalse(is_wide_char(' '))

        # CJK characters should return True
        self.assertTrue(is_wide_char('中'))
        self.assertTrue(is_wide_char('国'))
        self.assertTrue(is_wide_char('✅'))  # emoji

    def test_dock_text_truncates_without_breaking_wide_characters(self):
        text = truncate_to_width("Session: 很长的中文会话名称", 16)
        self.assertLessEqual(get_string_width(text), 16)
        rendered = format_dock_line("Context: ~123456 / 128000 (96.4%)", 24, "1;31")
        self.assertIn("\033[1;31m", rendered)
        self.assertIn("...", rendered)

    @patch('agent.tui_widgets.WINDOWS', True)
    @patch('sys.stdout.isatty', return_value=True)
    @patch('msvcrt.getwch')
    def test_select_menu_navigation(self, mock_getwch, mock_isatty):
        # We simulate:
        # 1. Down Arrow prefix ('\xe0')
        # 2. Down Arrow code ('P')
        # 3. Enter ('\r')
        mock_getwch.side_effect = ['\xe0', 'P', '\r']

        options = ["Yes", "No", "Cancel"]
        # Default index = 0, Down Arrow moves it to index 1 ("No")
        selected_idx = select_menu("Test Prompt", options, default_index=0)

        self.assertEqual(selected_idx, 1)

    @patch('agent.tui_widgets.WINDOWS', True)
    @patch('sys.stdout.isatty', return_value=True)
    @patch('msvcrt.getwch')
    def test_select_menu_wrap_around(self, mock_getwch, mock_isatty):
        # We simulate:
        # 1. Up Arrow prefix ('\xe0')
        # 2. Up Arrow code ('H')
        # 3. Enter ('\r')
        mock_getwch.side_effect = ['\xe0', 'H', '\r']

        options = ["Yes", "No", "Cancel"]
        # Default index = 0, Up Arrow wraps it around to index 2 ("Cancel")
        selected_idx = select_menu("Test Prompt", options, default_index=0)

        self.assertEqual(selected_idx, 2)


class _ComposerHarness(App[None]):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield Composer(id="composer", placeholder="type here...")

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        self.submitted.append(event.value)

    @property
    def composer(self) -> Composer:
        return self.query_one("#composer", Composer)


class TestComposer(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_enter_inserts_newline(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+enter")
            await pilot.pause()
            self.assertIn("\n", app.composer.text)

    async def test_shift_enter_inserts_newline(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.press("shift+enter")
            await pilot.pause()
            self.assertIn("\n", app.composer.text)

    async def test_enter_submits_non_empty_text(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.composer.text = "hello"
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.submitted, ["hello"])

    async def test_dynamic_height_grows_with_lines(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            initial = app.composer.styles.height
            app.composer.text = "line1\nline2\nline3"
            await pilot.pause()
            current = app.composer.styles.height
            # Height should switch from the default fraction to an explicit
            # cell value large enough to hold three lines plus border.
            self.assertNotEqual(current.unit, initial.unit)
            self.assertGreaterEqual(current.value, Composer.MIN_HEIGHT)
            self.assertLessEqual(current.value, Composer.MAX_HEIGHT)

    async def test_dynamic_height_grows_with_soft_wrapped_lines(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.composer.text = "W" * 500
            app.composer.move_cursor((0, 500))
            await pilot.pause(0.2)
            self.assertGreater(app.composer.virtual_size.height, 1)
            expected = min(
                Composer.MAX_HEIGHT,
                app.composer.virtual_size.height + Composer.FRAME_HEIGHT,
            )
            self.assertEqual(app.composer.styles.height.value, expected)

    async def test_eight_lines_fit_without_internal_scrolling(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.composer.text = "\n".join(f"line{i}" for i in range(8))
            app.composer.move_cursor((7, 5))
            await pilot.pause(0.2)
            self.assertEqual(app.composer.styles.height.value, Composer.MAX_HEIGHT)
            self.assertEqual(app.composer.max_scroll_y, 0)

    async def test_dynamic_height_resets_after_clear(self):
        app = _ComposerHarness()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.composer.text = "line1\nline2\nline3\nline4"
            await pilot.pause()
            grown = app.composer.styles.height.value
            self.assertGreater(grown, Composer.MIN_HEIGHT)
            app.composer.text = ""
            await pilot.pause()
            self.assertEqual(app.composer.styles.height.value, Composer.MIN_HEIGHT)


class TestMessageBody(unittest.IsolatedAsyncioTestCase):
    async def test_long_ascii_text_expands_vertically(self):
        app = _MessageBodyHarness("x" * 500)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            body = app.body
            # If the content were clipped horizontally into a single line,
            # virtual height would stay near 1. Wrapping should produce many rows.
            self.assertGreater(body.virtual_size.height, 5)

    async def test_long_chinese_text_expands_vertically(self):
        app = _MessageBodyHarness("中" * 200)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            self.assertGreater(app.body.virtual_size.height, 3)

    async def test_table_cell_content_is_preserved(self):
        cell = "a" * 100
        md = f"| col1 | col2 |\n|------|------|\n| {cell} | b |"
        app = _MessageBodyHarness(md)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.2)
            self.assertIn(cell, app.body._content)
            rendered = ""
            for widget in app.body.query("MarkdownTableCellContents"):
                size = widget.virtual_size
                lines = widget.render_lines(
                    Region(0, 0, max(widget.region.width, size.width), max(widget.region.height, size.height))
                )
                rendered += "".join(segment.text for line in lines for segment in line)
            self.assertEqual(rendered.count("a"), len(cell))

    async def test_streaming_appends_content(self):
        app = _MessageBodyHarness("hello")
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.body.append_content(" world")
            await pilot.pause()
            self.assertEqual(app.body._content, "hello world")

    async def test_code_block_content_is_preserved(self):
        code = "print('x' * 1000)"
        md = f"```python\n{code}\n```"
        app = _MessageBodyHarness(md)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            self.assertIn(code, app.body._content)

    async def test_plain_text_mode_uses_static(self):
        app = _MessageBodyHarness("plain", is_markdown=False)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            static = app.body.query_one(Static)
            self.assertIn("plain", str(static.render()))


if __name__ == "__main__":
    unittest.main()
