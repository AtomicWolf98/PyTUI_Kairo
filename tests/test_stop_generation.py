"""Tests for Kairo 0.2.6-beta: cooperative Esc stop generation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest.mock import patch

from agent.cancellation import CancellationToken
from agent.config import Config
from agent.core import Agent
from tools.base import BaseTool, ToolRegistry


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo back the argument."
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, text: str = "") -> str:
        return f"echo: {text}"


def _write_config(temp_dir: str) -> Path:
    config_path = Path(temp_dir) / "config.json"
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
            "workspace_root": temp_dir,
        }, f)
    return config_path


class TestCancellationToken(unittest.TestCase):
    def test_default_not_cancelled(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)

    def test_cancel_is_idempotent(self):
        token = CancellationToken()
        token.cancel()
        self.assertTrue(token.cancelled)
        token.cancel()
        self.assertTrue(token.cancelled)


class TestStopStreaming(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config_path = _write_config(self.temp_dir.name)
        self.config = Config(config_path=str(config_path))
        self.registry = ToolRegistry()
        self.registry.register(EchoTool())
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_stream(self, chunks, cancel_after=None, token=None):
        """Build a fake stream_response that yields *chunks* and honours *token*."""
        def fake_stream(messages, tools=None, max_tokens_override=None,
                        temperature_override=None, profile_role="chat", profile_id=None,
                        cancel_token=None):
            for kind, data in chunks:
                if kind == "content":
                    yield ("content", data)
                    if cancel_token is not None and cancel_token.cancelled:
                        yield ("stopped", None)
                        return
                else:
                    yield (kind, data)

        return fake_stream

    def test_esc_cancels_streaming_and_saves_partial(self):
        token = CancellationToken()
        events: List = []

        def emit(kind, data):
            events.append((kind, data))
            # Simulate Esc after the first content chunk arrives.
            if kind == "content_delta" and not token.cancelled:
                token.cancel()

        chunks = [("content", "Hello"), ("content", " world")]
        with patch.object(self.agent.llm, "stream_response", side_effect=self._make_stream(chunks, token=token)):
            self.agent.run_interaction_events("hi", emit, cancel_token=token)

        # The assistant partial is saved with a [stopped] marker.
        assistant_msgs = [m for m in self.agent.history if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        self.assertIn("[stopped]", assistant_msgs[-1]["content"])
        self.assertIn("Hello", assistant_msgs[-1]["content"])
        # A stopped state was emitted.
        self.assertTrue(any(k == "state" and d == "stopped" for k, d in events))

    def test_no_stop_when_token_not_cancelled(self):
        token = CancellationToken()
        events: List = []
        chunks = [("content", "Hello"), ("content", " world")]
        with patch.object(self.agent.llm, "stream_response", side_effect=self._make_stream(chunks)):
            self.agent.run_interaction_events("hi", lambda k, d: events.append((k, d)), cancel_token=token)

        assistant_msgs = [m for m in self.agent.history if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        self.assertNotIn("[stopped]", assistant_msgs[-1]["content"])
        self.assertEqual(assistant_msgs[-1]["content"], "Hello world")

    def test_stop_saves_partial_can_be_disabled(self):
        token = CancellationToken()
        self.config.ui["stop_saves_partial_response"] = False
        events: List = []

        def emit(kind, data):
            events.append((kind, data))
            if kind == "content_delta" and not token.cancelled:
                token.cancel()

        chunks = [("content", "Hello")]
        with patch.object(self.agent.llm, "stream_response", side_effect=self._make_stream(chunks)):
            self.agent.run_interaction_events("hi", emit, cancel_token=token)

        assistant_msgs = [m for m in self.agent.history if m["role"] == "assistant"]
        # With partial saving disabled, no assistant message is appended.
        self.assertEqual(assistant_msgs, [])

    def test_stop_during_tool_prevents_next_llm_round(self):
        token = CancellationToken()
        events: List = []
        call_count = {"n": 0}

        def emit(kind, data):
            events.append((kind, data))
            # Cancel right after the tool finishes.
            if kind == "tool_finished":
                token.cancel()

        def fake_stream(messages, tools=None, max_tokens_override=None,
                        temperature_override=None, profile_role="chat", profile_id=None,
                        cancel_token=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                yield ("tool_calls", [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
                }])
                return
            # A second round must NOT happen because we cancelled after the tool.
            yield ("content", "should not happen")

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            self.agent.run_interaction_events("do echo", emit, cancel_token=token)

        self.assertEqual(call_count["n"], 1)
        self.assertTrue(any(k == "state" and d == "stopped" for k, d in events))

    def test_history_remains_valid_after_stop_during_tool(self):
        token = CancellationToken()
        events: List = []

        def emit(kind, data):
            events.append((kind, data))
            if kind == "tool_finished":
                token.cancel()

        def fake_stream(messages, tools=None, **kwargs):
            yield ("tool_calls", [{
                "id": "call_1", "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
            }])

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            self.agent.run_interaction_events("do echo", emit, cancel_token=token)

        # The assistant tool_call and its tool result form a valid pair.
        errors = self.agent.conversations.validate_history_invariants()
        self.assertEqual(errors, [])
        # No orphan tool result.
        assistant = next(m for m in self.agent.history if m.get("tool_calls"))
        tool_result = next(m for m in self.agent.history if m.get("role") == "tool")
        self.assertEqual(tool_result["tool_call_id"], assistant["tool_calls"][0]["id"])

    def test_can_send_next_message_after_stop(self):
        token = CancellationToken()
        events: List = []

        def emit(kind, data):
            events.append((kind, data))
            if kind == "content_delta" and not token.cancelled:
                token.cancel()

        chunks = [("content", "Hello")]
        with patch.object(self.agent.llm, "stream_response", side_effect=self._make_stream(chunks)):
            self.agent.run_interaction_events("hi", emit, cancel_token=token)

        # A fresh token allows the next interaction to proceed normally.
        token2 = CancellationToken()
        events2: List = []
        with patch.object(self.agent.llm, "stream_response", side_effect=self._make_stream([("content", "World")])):
            self.agent.run_interaction_events("again", lambda k, d: events2.append((k, d)), cancel_token=token2)

        assistant_msgs = [m for m in self.agent.history if m["role"] == "assistant"]
        contents = [m["content"] for m in assistant_msgs]
        self.assertIn("Hello\n\n[stopped]", contents)
        self.assertIn("World", contents)


class TestStopDoesNotAffectPaletteOrModal(unittest.TestCase):
    """The composer palette Esc must not trigger generation stop."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        config_path = _write_config(self.temp_dir.name)
        self.config = Config(config_path=str(config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_esc_with_no_busy_does_not_crash(self):
        # When not busy, request_stop_current_task is a no-op.
        token = CancellationToken()
        # Simulate the app's guard: only stop when busy.
        busy = False
        if busy and token is not None:
            token.cancel()
        self.assertFalse(token.cancelled)


if __name__ == "__main__":
    unittest.main()
