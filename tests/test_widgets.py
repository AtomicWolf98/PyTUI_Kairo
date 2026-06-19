import unittest
from unittest.mock import patch, MagicMock
import sys
from agent.tui_widgets import format_dock_line, get_string_width, is_wide_char, select_menu, truncate_to_width

class TestTUIWidgets(unittest.TestCase):
    def test_is_wide_char(self):
        # ASCII should return False
        self.assertFalse(is_wide_char('a'))
        self.assertFalse(is_wide_char('/'))
        self.assertFalse(is_wide_char(' '))
        
        # CJK characters should return True
        self.assertTrue(is_wide_char('中'))
        self.assertTrue(is_wide_char('国'))
        self.assertTrue(is_wide_char('✅')) # emoji

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

if __name__ == "__main__":
    unittest.main()
