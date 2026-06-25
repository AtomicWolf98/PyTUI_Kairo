"""Tests for Kairo 0.2.6-beta: strict OpenAI message packing layer."""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from agent.message_packer import pack_messages_for_provider, validate_provider_payload


def _system(content: str, name: str = "") -> Dict[str, Any]:
    msg = {"role": "system", "content": content}
    if name:
        msg["name"] = name
    return msg


class TestMessagePacker(unittest.TestCase):
    def test_default_history_folds_into_single_system(self):
        history = [
            _system("You are Kairo."),
            _system("Kairo runtime state:\n- workspace: /tmp", name="kairo_runtime_state"),
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        packed, warnings = pack_messages_for_provider(history)
        self.assertEqual(len(packed), 3)
        self.assertEqual(packed[0]["role"], "system")
        self.assertIn("You are Kairo.", packed[0]["content"])
        self.assertIn("Kairo runtime state", packed[0]["content"])
        self.assertEqual(packed[1]["role"], "user")
        self.assertEqual(packed[2]["role"], "assistant")
        # No system after index 0.
        for msg in packed[1:]:
            self.assertNotEqual(msg["role"], "system")
        self.assertEqual(warnings, [])

    def test_summary_folds_into_first_system(self):
        history = [
            _system("You are Kairo."),
            _system("kairo_runtime state", name="kairo_runtime_state"),
            _system("[Conversation Summary]\nEarlier discussion."),
            {"role": "user", "content": "continue"},
        ]
        packed, warnings = pack_messages_for_provider(history)
        self.assertEqual(packed[0]["role"], "system")
        self.assertIn("[Conversation Summary]", packed[0]["content"])
        self.assertIn("Earlier discussion", packed[0]["content"])
        self.assertEqual(warnings, [])

    def test_no_system_after_first_message_in_output(self):
        history = [
            _system("main"),
            {"role": "user", "content": "hi"},
            _system("rogue system after user"),
            {"role": "assistant", "content": "reply"},
        ]
        packed, warnings = pack_messages_for_provider(history)
        self.assertEqual(packed[0]["role"], "system")
        for msg in packed[1:]:
            self.assertNotEqual(msg["role"], "system")
        # The rogue system is folded and a warning is emitted.
        self.assertTrue(any("folded" in w for w in warnings))
        self.assertIn("rogue system after user", packed[0]["content"])

    def test_tool_call_result_pairing_preserved(self):
        history = [
            _system("main"),
            {"role": "user", "content": "do thing"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "file contents"},
            {"role": "assistant", "content": "done"},
        ]
        packed, warnings = pack_messages_for_provider(history)
        # system + user + assistant(tool_calls) + tool + assistant
        self.assertEqual(len(packed), 5)
        self.assertEqual(packed[2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(packed[3]["role"], "tool")
        self.assertEqual(packed[3]["tool_call_id"], "call_1")
        self.assertEqual(warnings, [])

    def test_internal_fields_stripped(self):
        history = [
            _system("main"),
            {"role": "user", "content": "hi", "_internal_debug": True, "hidden_reasoning": "secret"},
        ]
        packed, _ = pack_messages_for_provider(history)
        self.assertNotIn("_internal_debug", packed[1])
        self.assertNotIn("hidden_reasoning", packed[1])
        self.assertEqual(packed[1]["content"], "hi")

    def test_orphan_tool_result_warns(self):
        history = [
            _system("main"),
            {"role": "tool", "tool_call_id": "orphan", "name": "x", "content": "result"},
        ]
        _, warnings = pack_messages_for_provider(history)
        self.assertTrue(any("orphan tool result" in w for w in warnings))

    def test_missing_tool_result_warns(self):
        history = [
            _system("main"),
            {"role": "user", "content": "do thing"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_x", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
        ]
        _, warnings = pack_messages_for_provider(history)
        self.assertTrue(any("no matching tool result" in w for w in warnings))

    def test_empty_history_returns_empty(self):
        packed, warnings = pack_messages_for_provider([])
        self.assertEqual(packed, [])
        self.assertEqual(warnings, [])

    def test_no_system_inserts_empty_leading_system(self):
        history = [{"role": "user", "content": "hi"}]
        packed, warnings = pack_messages_for_provider(history)
        self.assertEqual(packed[0]["role"], "system")
        self.assertEqual(packed[0]["content"], "")
        self.assertTrue(any("no system message" in w for w in warnings))

    def test_non_strict_keeps_systems_in_place(self):
        history = [
            _system("main"),
            _system("runtime", name="kairo_runtime_state"),
            {"role": "user", "content": "hi"},
        ]
        packed, warnings = pack_messages_for_provider(history, strict_openai=False)
        # Permissive: systems kept in place (still stripped of internal fields).
        self.assertEqual(packed[0]["role"], "system")
        self.assertEqual(packed[1]["role"], "system")
        self.assertEqual(packed[2]["role"], "user")
        self.assertEqual(warnings, [])

    def test_non_strict_keeps_late_systems_in_place(self):
        history = [
            _system("main"),
            {"role": "user", "content": "hi"},
            _system("late system"),
            {"role": "assistant", "content": "reply"},
        ]
        packed, warnings = pack_messages_for_provider(history, strict_openai=False)
        self.assertEqual([m["role"] for m in packed], ["system", "user", "system", "assistant"])
        self.assertEqual(packed[2]["content"], "late system")
        self.assertTrue(any("folded" in w for w in warnings))

    def test_does_not_mutate_input(self):
        history = [
            _system("main"),
            {"role": "user", "content": "hi", "_internal": True},
        ]
        original = [dict(m) for m in history]
        pack_messages_for_provider(history)
        self.assertEqual(history, original)


class TestValidateProviderPayload(unittest.TestCase):
    def test_valid_payload(self):
        messages = [
            {"role": "system", "content": "main"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "reply"},
        ]
        self.assertEqual(validate_provider_payload(messages), [])

    def test_system_after_index_zero(self):
        messages = [
            {"role": "system", "content": "main"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "rogue"},
        ]
        errors = validate_provider_payload(messages)
        self.assertTrue(any("after leading system" in e for e in errors))

    def test_first_not_system(self):
        messages = [{"role": "user", "content": "hi"}]
        errors = validate_provider_payload(messages)
        self.assertTrue(any("not role=system" in e for e in errors))

    def test_empty_payload(self):
        self.assertEqual(len(validate_provider_payload([])), 1)


class TestPackerIntegrationWithRunner(unittest.TestCase):
    """Verify the runner packs the history before streaming."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        from agent.config import Config
        from agent.core import Agent
        from tools.base import ToolRegistry

        self.temp_dir = tempfile.TemporaryDirectory()
        config_path = Path(self.temp_dir.name) / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({
                "llm": {
                    "active_profile": "p1/m1",
                    "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                    "profiles": [
                        {"id": "p1/m1", "base_url": "https://p1.example.com/v1", "api_key": "s", "model": "m1"},
                    ],
                },
                "sessions": {"enabled": False},
                "workspace_root": str(self.temp_dir.name),
            }, f)
        self.config = Config(config_path=str(config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)
        self.temp_dir = self.temp_dir

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_runner_packs_history_before_stream(self):
        from unittest.mock import patch

        captured: List[Dict[str, Any]] = []

        def fake_stream(messages, tools=None, max_tokens_override=None,
                        temperature_override=None, profile_role="chat", profile_id=None,
                        cancel_token=None):
            captured.extend(messages)
            yield ("content", "ok")
            yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})

        # Build a history with multiple system messages.
        self.agent.history.append({"role": "user", "content": "hello"})

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            events: List = []

            def emit(kind, data):
                events.append((kind, data))

            self.agent.run_interaction_events("hello", emit)

        # The captured payload must have exactly one leading system message.
        self.assertEqual(captured[0]["role"], "system")
        for msg in captured[1:]:
            self.assertNotEqual(msg["role"], "system")
        # The main system instruction and runtime state are folded together.
        self.assertIn("Kairo", captured[0]["content"])
        self.assertIn("runtime state", captured[0]["content"])

    def test_strict_packing_disabled_passes_through(self):
        from unittest.mock import patch

        captured: List[Dict[str, Any]] = []

        def fake_stream(messages, tools=None, max_tokens_override=None,
                        temperature_override=None, profile_role="chat", profile_id=None,
                        cancel_token=None):
            captured.extend(messages)
            yield ("content", "ok")
            yield ("usage", {"prompt_tokens": 1, "completion_tokens": 1})

        self.config.llm["strict_message_packing"] = False
        self.agent.history.append({"role": "user", "content": "hello"})

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            def emit(kind, data):
                pass

            self.agent.run_interaction_events("hello", emit)

        # Non-strict: multiple system messages kept in place.
        roles = [m["role"] for m in captured]
        self.assertEqual(roles[0], "system")
        self.assertEqual(roles[1], "system")


if __name__ == "__main__":
    unittest.main()
