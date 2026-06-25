"""Tests for Kairo 0.2.6-beta: unified model switching transaction."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.commands import CommandDispatcher
from agent.config import Config
from agent.core import Agent
from agent.profile_resolver import resolve_profile
from tools.base import ToolRegistry


def _write_config(temp_dir: str, data: dict) -> Path:
    config_path = Path(temp_dir) / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return config_path


class TestModelSwitchingProfiles(unittest.TestCase):
    """``/model`` over llm.profiles[] structure."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = _write_config(self.temp_dir.name, {
            "llm": {
                "active_profile": "alpha/a-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {
                        "id": "alpha/a-model",
                        "label": "Alpha",
                        "provider": "alpha",
                        "base_url": "https://alpha.example.com/v1",
                        "api_key": "alpha-secret",
                        "model": "a-model",
                        "context_window": 32000,
                        "max_tokens": 4000,
                    },
                    {
                        "id": "beta/b-model",
                        "label": "Beta",
                        "provider": "beta",
                        "base_url": "https://beta.example.com/v1",
                        "api_key": "beta-secret",
                        "model": "b-model",
                        "context_window": 64000,
                        "max_tokens": 8000,
                    },
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        })
        self.config = Config(config_path=str(self.config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_switch_without_role_updates_active_profile(self):
        result = self.agent.switch_model_profile("beta/b-model", source="test")
        self.assertTrue(result.success)
        self.assertEqual(self.config.llm.get("active_profile"), "beta/b-model")
        self.assertNotIn("chat", self.config.model_roles)
        resolved = resolve_profile(self.config, role="chat")
        self.assertEqual(resolved.id, "beta/b-model")
        self.assertEqual(resolved.model, "b-model")
        self.assertEqual(resolved.base_url, "https://beta.example.com/v1")

    def test_next_chat_request_uses_new_profile(self):
        self.agent.switch_model_profile("beta/b-model", source="test")
        resolved = resolve_profile(self.config, role="chat")
        self.assertEqual(resolved.id, "beta/b-model")
        self.assertEqual(resolved.api_key, "beta-secret")

    def test_switch_with_role_override_updates_chat_role(self):
        self.config.model_roles = {"chat": "alpha/a-model", "plan": "alpha/a-model"}
        self.config.save()
        result = self.agent.switch_model_profile("beta/b-model", source="test")
        self.assertTrue(result.success)
        # The chat role must move to the new profile so the resolver uses it.
        self.assertEqual(self.config.model_roles.get("chat"), "beta/b-model")
        # active_profile is kept consistent too so UI surfaces agree.
        self.assertEqual(self.config.llm.get("active_profile"), "beta/b-model")
        resolved = resolve_profile(self.config, role="chat")
        self.assertEqual(resolved.id, "beta/b-model")
        self.assertTrue(result.data.get("role_updated"))

    def test_plan_role_not_accidentally_changed(self):
        self.config.model_roles = {"chat": "alpha/a-model", "plan": "alpha/a-model", "compress": "alpha/a-model"}
        self.config.save()
        self.agent.switch_model_profile("beta/b-model", source="test")
        self.assertEqual(self.config.model_roles.get("plan"), "alpha/a-model")
        self.assertEqual(self.config.model_roles.get("compress"), "alpha/a-model")
        plan_resolved = resolve_profile(self.config, role="plan")
        self.assertEqual(plan_resolved.id, "alpha/a-model")

    def test_context_window_updates_immediately(self):
        self.agent.switch_model_profile("beta/b-model", source="test")
        self.assertEqual(self.config.context_window, 64000)
        # ConversationManager and token trackers follow the new window.
        self.assertEqual(self.agent.conversations.context_window, 64000)
        for session in self.agent.conversations.sessions:
            self.assertEqual(session.token_tracker.context_window, 64000)

    def test_runtime_state_reflects_new_profile(self):
        self.agent.switch_model_profile("beta/b-model", source="test")
        self.assertEqual(
            self.agent.conversations._runtime_state["model_profile"],
            self.config.active_model_profile,
        )
        # The runtime state system message in history is updated.
        runtime_msg = next(
            (m for m in self.agent.history if m.get("name") == "kairo_runtime_state"),
            None,
        )
        self.assertIsNotNone(runtime_msg)
        self.assertIn(self.config.active_model_profile, runtime_msg["content"])

    def test_dispatcher_model_command_carries_role_override_hint(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/model")
        self.assertTrue(result.interactive)
        self.assertEqual(result.data["mode"], "profile")
        self.assertFalse(result.data["role_override"])
        self.config.model_roles = {"chat": "beta/b-model"}
        result = dispatcher.dispatch("/model")
        self.assertTrue(result.data["role_override"])
        # Default index points to the resolved chat profile.
        self.assertEqual(result.data["profiles"][result.data["default_index"]], "beta/b-model")

    def test_switch_by_label(self):
        result = self.agent.switch_model_profile("Beta", source="test")
        self.assertTrue(result.success)
        self.assertEqual(self.config.llm.get("active_profile"), "beta/b-model")

    def test_switch_unknown_profile_fails(self):
        result = self.agent.switch_model_profile("nope/missing", source="test")
        self.assertFalse(result.success)


class TestModelSwitchingLegacy(unittest.TestCase):
    """``/model`` over legacy llm.providers[] structure."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = _write_config(self.temp_dir.name, {
            "llm": {
                "active_provider": "openai",
                "active_model": "gpt-4o",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "providers": [
                    {
                        "name": "openai",
                        "base_url": "https://openai.test/v1",
                        "api_key": "openai_key",
                        "models": [{"name": "gpt-4o", "temperature": 0.2, "max_tokens": 4000, "context_window": 128000}],
                    },
                    {
                        "name": "local",
                        "base_url": "https://local.test/v1",
                        "api_key": "local_key",
                        "models": [{"name": "local-model", "temperature": 0.6, "max_tokens": 8000, "context_window": 64000}],
                    },
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        })
        self.config = Config(config_path=str(self.config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_legacy_switch_updates_active_model(self):
        result = self.agent.switch_model_profile("local / local-model", source="test")
        self.assertTrue(result.success)
        self.assertEqual(self.config.llm["active_provider"], "local")
        self.assertEqual(self.config.llm["active_model"], "local-model")
        resolved = resolve_profile(self.config, role="chat")
        self.assertEqual(resolved.model, "local-model")
        self.assertEqual(self.config.context_window, 64000)

    def test_legacy_switch_keeps_role_consistent(self):
        self.config.model_roles = {"chat": "openai / gpt-4o"}
        self.config.save()
        self.agent.switch_model_profile("local / local-model", source="test")
        self.assertEqual(self.config.model_roles.get("chat"), "local / local-model")
        resolved = resolve_profile(self.config, role="chat")
        self.assertEqual(resolved.model, "local-model")


class TestModelSwitchRequestPayload(unittest.TestCase):
    """Verify the LLM request payload would use the switched profile."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = _write_config(self.temp_dir.name, {
            "llm": {
                "active_profile": "alpha/a-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {"id": "alpha/a-model", "base_url": "https://alpha.example.com/v1", "api_key": "alpha-secret", "model": "a-model"},
                    {"id": "beta/b-model", "base_url": "https://beta.example.com/v1", "api_key": "beta-secret", "model": "b-model"},
                ],
            },
            "model_roles": {"chat": "alpha/a-model"},
            "workspace_root": str(self.temp_dir.name),
        })
        self.config = Config(config_path=str(self.config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stream_response_uses_switched_profile(self):
        captured: dict = {}

        def fake_stream(messages, tools=None, max_tokens_override=None, temperature_override=None,
                        profile_role="chat", profile_id=None, cancel_token=None):
            resolved = resolve_profile(self.config, profile_id=profile_id, role=profile_role)
            captured["model"] = resolved.model if resolved else None
            captured["base_url"] = resolved.base_url if resolved else None
            captured["api_key"] = resolved.api_key if resolved else None
            yield ("content", "ok")

        with patch.object(self.agent.llm, "stream_response", side_effect=fake_stream):
            # Switch via the unified transaction, then drive one interaction.
            self.agent.switch_model_profile("beta/b-model", source="test")
            events: list = []

            def emit(kind, data):
                events.append((kind, data))

            self.agent.run_interaction_events("hello", emit)

        self.assertEqual(captured["model"], "b-model")
        self.assertEqual(captured["base_url"], "https://beta.example.com/v1")
        self.assertEqual(captured["api_key"], "beta-secret")


if __name__ == "__main__":
    unittest.main()
