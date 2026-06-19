import unittest
from unittest.mock import patch

from agent.config import Config
from agent.context_manager import ConversationManager, SUMMARY_PREFIX, split_complete_turns
from agent.core import Agent
from tools.base import ToolRegistry


def add_turn(history, index, with_tool=False):
    history.append({"role": "user", "content": f"user-{index}"})
    if with_tool:
        history.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call-{index}",
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            }],
        })
        history.append({
            "role": "tool",
            "tool_call_id": f"call-{index}",
            "name": "demo",
            "content": f"tool-{index}",
        })
    history.append({"role": "assistant", "content": f"assistant-{index}"})


class TestConversationManager(unittest.TestCase):
    def setUp(self):
        self.manager = ConversationManager("system", context_window=1000)

    def test_sessions_keep_independent_history_and_usage(self):
        first = self.manager.active
        add_turn(first.history, 1)
        first.token_tracker.add_tokens(10, 5)
        self.manager.refresh_context()

        second = self.manager.create_session("Second")
        add_turn(second.history, 2)
        second.token_tracker.add_tokens(20, 7)
        self.manager.refresh_context()

        self.assertTrue(self.manager.switch_session(first.id))
        self.assertIn("user-1", str(self.manager.active.history))
        self.assertNotIn("user-2", str(self.manager.active.history))
        self.assertEqual(self.manager.active.token_tracker.session_input_tokens, 10)

    def test_compression_keeps_recent_complete_turns(self):
        for index in range(1, 7):
            add_turn(self.manager.active.history, index, with_tool=index == 5)

        source, retained = self.manager.compression_parts(preserve_recent_turns=2)
        self.assertIn("user-1", str(source))
        self.assertNotIn("user-5", str(source))
        self.assertIn("call-5", str(retained))
        self.assertIn("tool-5", str(retained))

        self.manager.apply_summary("important summary", retained)
        prefix, turns = split_complete_turns(self.manager.active.history)
        self.assertEqual(len(turns), 2)
        self.assertTrue(prefix[1]["content"].startswith(SUMMARY_PREFIX))

    def test_rolling_summary_replaces_previous_summary(self):
        for index in range(1, 4):
            add_turn(self.manager.active.history, index)
        source, retained = self.manager.compression_parts(1)
        self.manager.apply_summary("first summary", retained)
        add_turn(self.manager.active.history, 4)

        source, retained = self.manager.compression_parts(1)
        self.assertIn("first summary", str(source))
        self.manager.apply_summary("second summary", retained)
        summary_messages = [
            message for message in self.manager.active.history
            if str(message.get("content", "")).startswith(SUMMARY_PREFIX)
        ]
        self.assertEqual(len(summary_messages), 1)
        self.assertIn("second summary", summary_messages[0]["content"])

    def test_trim_removes_whole_turns_and_keeps_latest_user(self):
        for index in range(1, 5):
            add_turn(self.manager.active.history, index, with_tool=index == 2)
        latest_turn_size = self.manager.estimator.estimate_messages(
            [self.manager.active.history[0]] + split_complete_turns(self.manager.active.history)[1][-1]
        )

        removed, fits = self.manager.trim_oldest_to_budget(latest_turn_size + 10)
        self.assertTrue(fits)
        self.assertGreater(removed, 0)
        serialized = str(self.manager.active.history)
        self.assertIn("user-4", serialized)
        self.assertNotIn("call-2", serialized)
        self.assertNotIn("tool-2", serialized)


class TestAgentContextManagement(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.config.context_window = 128000
        self.config.max_tokens = 4000
        self.config.context_management = {
            "enabled": True,
            "auto_compress": True,
            "trigger_percent": 85.0,
            "target_percent": 60.0,
            "preserve_recent_turns": 4,
        }
        self.agent = Agent(self.config, ToolRegistry())

    def test_manual_compress_keeps_four_recent_turns(self):
        for index in range(1, 7):
            add_turn(self.agent.history, index)

        def fake_stream(*args, **kwargs):
            yield "content", "summary text"
            yield "usage", {"prompt_tokens": 100, "completion_tokens": 20}

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            success, message = self.agent.compress_context(manual=True)

        self.assertTrue(success)
        self.assertIn("Manual context compression completed", message)
        _, turns = split_complete_turns(self.agent.history)
        self.assertEqual(len(turns), 4)
        self.assertEqual(self.agent.conversations.active.compression_count, 1)

    def test_context_error_retries_only_once(self):
        responses = [
            [("context_error", "context length exceeded")],
            [("content", "recovered")],
        ]

        def fake_stream(*args, **kwargs):
            yield from responses.pop(0)

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream) as stream:
            self.agent.run_interaction("hello")

        self.assertEqual(stream.call_count, 2)
        self.assertEqual(self.agent.history[-1]["content"], "recovered")

    def test_automatic_fallback_trims_old_turns_but_keeps_current_user(self):
        self.config.context_window = 6000
        self.config.max_tokens = 1000
        self.config.context_management.update({
            "trigger_percent": 20.0,
            "target_percent": 30.0,
        })
        self.agent.conversations.set_context_window(6000)
        for index in range(1, 7):
            self.agent.history.append({"role": "user", "content": f"user-{index} " + "x" * 1200})
            self.agent.history.append({"role": "assistant", "content": "y" * 1200})

        with patch.object(self.agent, "compress_context", return_value=(False, "failed")):
            self.assertTrue(self.agent.ensure_context_capacity([]))

        serialized = str(self.agent.history)
        self.assertIn("user-6", serialized)
        self.assertNotIn("user-1", serialized)
        self.assertLessEqual(self.agent.token_tracker.context_used_tokens, 1800)

    def test_disabled_management_allows_safe_request_above_trigger(self):
        self.config.context_window = 5000
        self.config.max_tokens = 500
        self.config.context_management.update({
            "enabled": False,
            "trigger_percent": 1.0,
        })
        self.agent.conversations.set_context_window(5000)
        self.agent.history.append({"role": "user", "content": "hello" * 200})

        self.assertTrue(self.agent.ensure_context_capacity([]))


if __name__ == "__main__":
    unittest.main()
