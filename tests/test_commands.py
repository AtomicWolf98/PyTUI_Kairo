import unittest
from unittest.mock import MagicMock, patch
from agent.config import Config
from agent.core import Agent
from tools.base import ToolRegistry

class TestAgentCommands(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.config.llm = {
            "active_provider": "openai",
            "active_model": "gpt-4o",
            "defaults": {
                "temperature": 0.2,
                "max_tokens": 4000,
                "context_window": 128000,
            },
            "providers": [
                {
                    "name": "openai",
                    "base_url": "https://openai.test/v1",
                    "api_key": "openai_key",
                    "models": [
                        {
                            "name": "gpt-4o",
                            "temperature": 0.2,
                            "max_tokens": 4000,
                            "context_window": 128000,
                        }
                    ],
                },
                {
                    "name": "local",
                    "base_url": "https://local.test/v1",
                    "api_key": "local_key",
                    "models": [
                        {
                            "name": "local-test-model",
                            "temperature": 0.6,
                            "max_tokens": 8000,
                            "context_window": 64000,
                        }
                    ],
                },
            ],
        }
        self.config.select_active_model("openai", "gpt-4o")
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def test_undo_empty_history(self):
        # Initial history only has system instruction
        self.assertEqual(len(self.agent.history), 1)
        self.agent.history = [{"role": "system", "content": "instruction"}]
        
        with patch.object(self.agent.console, 'print') as mock_print:
            handled = self.agent.handle_command("/undo")
            self.assertTrue(handled)
            self.assertEqual(len(self.agent.history), 1)
            mock_print.assert_called_with("[bold yellow]No conversation turn to undo.[/bold yellow]")

    def test_undo_with_conversation(self):
        # Set up a conversation: system, user, assistant
        self.agent.history = [
            {"role": "system", "content": "instruction"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"}
        ]
        
        with patch.object(self.agent.console, 'print') as mock_print:
            handled = self.agent.handle_command("/undo")
            self.assertTrue(handled)
            # Should roll back to only system instruction
            self.assertEqual(len(self.agent.history), 1)
            self.assertEqual(self.agent.history[0]["role"], "system")

    def test_undo_with_tool_calls(self):
        # Set up a conversation: system, user, assistant (tool call), tool result, assistant (final response)
        self.agent.history = [
            {"role": "system", "content": "instruction"},
            {"role": "user", "content": "run task"},
            {"role": "assistant", "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "tc1", "name": "test_tool", "content": "success"},
            {"role": "assistant", "content": "all done"}
        ]
        
        with patch.object(self.agent.console, 'print') as mock_print:
            handled = self.agent.handle_command("/undo")
            self.assertTrue(handled)
            # Should roll back everything from the last user message onwards
            self.assertEqual(len(self.agent.history), 1)
            self.assertEqual(self.agent.history[0]["role"], "system")

    def test_new_and_sessions_switch_independent_conversations(self):
        self.agent.history.append({"role": "user", "content": "first conversation"})
        first_session_id = self.agent.conversations.active_session_id

        self.assertTrue(self.agent.handle_command("/new Work Session"))
        self.assertEqual(self.agent.active_session_name, "Work Session")
        self.assertNotIn("first conversation", str(self.agent.history))

        with patch('agent.tui_widgets.select_menu', return_value=0):
            self.assertTrue(self.agent.handle_command("/sessions"))
        self.assertEqual(self.agent.conversations.active_session_id, first_session_id)
        self.assertIn("first conversation", str(self.agent.history))

    def test_sessions_ignores_invalid_selection_index(self):
        original_session_id = self.agent.conversations.active_session_id

        with patch('agent.tui_widgets.select_menu', return_value=99):
            with patch.object(self.agent.console, 'print') as mock_print:
                self.assertTrue(self.agent.handle_command("/sessions"))

        self.assertEqual(self.agent.conversations.active_session_id, original_session_id)
        mock_print.assert_called_with("[bold yellow]Session switch cancelled: invalid selection.[/bold yellow]")

    def test_clear_only_resets_active_conversation(self):
        self.agent.history.append({"role": "user", "content": "keep in first"})
        first_session_id = self.agent.conversations.active_session_id
        self.agent.handle_command("/new Second")
        self.agent.history.append({"role": "user", "content": "clear me"})

        self.agent.handle_command("/clear")
        self.assertEqual(len(self.agent.history), 1)
        self.agent.conversations.switch_session(first_session_id)
        self.assertIn("keep in first", str(self.agent.history))

    def test_workspace_command_is_handled_in_plain_mode(self):
        with patch.object(self.agent.console, "print") as mock_print:
            self.assertTrue(self.agent.handle_command("/workspace"))
        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("Current workspace", output)
        self.assertIn("/workspace move", output)

    @patch('agent.tui_widgets.select_menu', return_value=1) # mock select local profile (index 1)
    @patch('agent.config.Config.save')
    def test_model_change(self, mock_save, mock_select_menu):
        handled = self.agent.handle_command("/model")
        self.assertTrue(handled)
        self.assertEqual(self.config.active_model_profile, "local / local-test-model")
        self.assertEqual(self.config.active_provider, "local")
        self.assertEqual(self.config.active_model, "local-test-model")
        self.assertEqual(self.config.model, "local-test-model")
        self.assertEqual(self.config.base_url, "https://local.test/v1")
        self.assertEqual(self.config.api_key, "local_key")
        self.assertEqual(self.config.max_tokens, 8000)
        self.assertEqual(self.config.context_window, 64000)
        self.assertEqual(self.agent.token_tracker.context_window, 64000)
        mock_select_menu.assert_called_once_with(
            "Select provider / model:",
            ["openai / gpt-4o", "local / local-test-model"],
            default_index=0,
        )
        mock_save.assert_called_once()

if __name__ == "__main__":
    unittest.main()
