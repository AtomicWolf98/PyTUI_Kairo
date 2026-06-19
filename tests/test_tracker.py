import unittest
from agent.token_tracker import TokenTracker

class TestTokenTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = TokenTracker(context_window=1000)

    def test_estimate_tokens_empty(self):
        self.assertEqual(self.tracker.estimate_tokens(""), 0)
        self.assertEqual(self.tracker.estimate_tokens(None), 0)

    def test_estimate_tokens_ascii(self):
        # 12 characters ASCII -> roughly 3 tokens (12 // 4)
        self.assertEqual(self.tracker.estimate_tokens("hello world!"), 3)
        # 15 characters ASCII -> roughly 3 tokens (15 // 4)
        self.assertEqual(self.tracker.estimate_tokens("hello world!!!!"), 3)

    def test_estimate_tokens_cjk(self):
        # 2 CJK characters -> 2 tokens
        self.assertEqual(self.tracker.estimate_tokens("你好"), 2)

    def test_estimate_tokens_mixed(self):
        # 2 CJK characters (2 tokens) + 12 ASCII characters (12 // 4 = 3 tokens) = 5 tokens
        self.assertEqual(self.tracker.estimate_tokens("你好 hello world!"), 5)

    def test_add_tokens(self):
        self.tracker.add_tokens(100, 50)
        self.assertEqual(self.tracker.session_input_tokens, 100)
        self.assertEqual(self.tracker.session_output_tokens, 50)
        self.assertEqual(self.tracker.total_tokens, 150)
        self.tracker.set_context_used(150)
        self.assertEqual(self.tracker.context_percent, 15.0)

    def test_add_text(self):
        self.tracker.add_text("hello world!", "你好")
        # "hello world!" -> 3 input tokens
        # "你好" -> 2 output tokens
        self.assertEqual(self.tracker.session_input_tokens, 3)
        self.assertEqual(self.tracker.session_output_tokens, 2)
        self.assertEqual(self.tracker.total_tokens, 5)

    def test_reset(self):
        self.tracker.add_tokens(100, 50)
        self.tracker.reset()
        self.assertEqual(self.tracker.total_tokens, 0)

if __name__ == "__main__":
    unittest.main()
