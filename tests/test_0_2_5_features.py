"""Tests for Kairo 0.2.5 features: profiles, keys, roles, bookmarks, sessions, doctor."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.commands import CommandDispatcher
from agent.config import Config
from agent.config_editor import ConfigDraft
from agent.core import Agent
from agent.profile_resolver import (
    describe_key_source,
    list_profiles,
    mask_key,
    resolve_profile,
)
from agent.runtime_commands import (
    _resolve_workspace_target,
    handle_config_export,
    handle_doctor,
    handle_key_clear,
    handle_key_reveal,
    handle_key_set,
    handle_keys,
    handle_role_clear,
    handle_role_set,
    handle_roles,
    handle_workspace_remove,
    handle_workspace_save,
    handle_workspaces,
)
from tools.base import ToolRegistry


class TestProfileResolver(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.data = {
            "llm": {
                "active_profile": "minimax/MiniMax-M3",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "profiles": [
                    {
                        "id": "minimax/MiniMax-M3",
                        "label": "MiniMax M3",
                        "provider": "minimax",
                        "base_url": "https://api.minimaxi.com/v1",
                        "api_key": "profile-secret",
                        "api_key_env": "",
                        "model": "MiniMax-M3",
                        "temperature": 0.2,
                        "max_tokens": 40000,
                        "context_window": 128000,
                    },
                    {
                        "id": "deepseek/deepseek-chat",
                        "provider": "deepseek",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key": "",
                        "api_key_env": "DS_KEY",
                        "model": "deepseek-chat",
                        "temperature": 0.3,
                        "max_tokens": 8000,
                        "context_window": 128000,
                    },
                ],
            },
            "model_roles": {"chat": "minimax/MiniMax-M3", "plan": "deepseek/deepseek-chat"},
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f)

    def tearDown(self):
        self.temp_dir.cleanup()
        for key in ["DS_KEY"]:
            if key in os.environ:
                del os.environ[key]

    def test_mask_key(self):
        self.assertEqual(mask_key(""), "missing")
        self.assertEqual(mask_key("short"), "********")
        self.assertEqual(mask_key("sk-abcdef1234567890"), "sk...7890")

    def test_describe_key_source(self):
        self.assertEqual(describe_key_source("x", "env"), "env")
        self.assertEqual(describe_key_source("sk-abcdef1234567890", "file"), "inline (sk...7890)")
        self.assertEqual(describe_key_source("", "none"), "missing")

    def test_resolve_active_profile(self):
        config = Config(config_path=str(self.config_path))
        profile = resolve_profile(config)
        self.assertEqual(profile.id, "minimax/MiniMax-M3")
        self.assertEqual(profile.api_key, "profile-secret")
        self.assertEqual(profile.api_key_source, "file")

    def test_role_resolution(self):
        config = Config(config_path=str(self.config_path))
        profile = resolve_profile(config, role="plan")
        self.assertEqual(profile.id, "deepseek/deepseek-chat")
        self.assertEqual(profile.api_key, "")
        os.environ["DS_KEY"] = "env-secret"
        profile = resolve_profile(config, role="plan")
        self.assertEqual(profile.api_key, "env-secret")
        self.assertEqual(profile.api_key_source, "env")

    def test_explicit_profile_id_overrides_role(self):
        config = Config(config_path=str(self.config_path))
        profile = resolve_profile(config, profile_id="deepseek/deepseek-chat", role="chat")
        self.assertEqual(profile.id, "deepseek/deepseek-chat")

    def test_list_profiles(self):
        config = Config(config_path=str(self.config_path))
        profiles = list_profiles(config)
        self.assertEqual([p.id for p in profiles], ["minimax/MiniMax-M3", "deepseek/deepseek-chat"])


class TestConfigProfiles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.data = {
            "llm": {
                "active_provider": "alpha",
                "active_model": "alpha-1",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "providers": [
                    {
                        "name": "alpha",
                        "base_url": "https://alpha.example.com/v1",
                        "api_key": "alpha-secret",
                        "models": [{"name": "alpha-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}],
                    }
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_legacy_providers_convert_to_profiles(self):
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.llm.get("active_profile"), "")
        self.assertEqual(config.active_provider, "alpha")
        self.assertEqual(config.active_model, "alpha-1")
        # get_active_llm_settings uses profile resolver view.
        settings = config.get_active_llm_settings()
        self.assertEqual(settings["api_key"], "alpha-secret")
        self.assertEqual(settings["base_url"], "https://alpha.example.com/v1")

    def test_new_profile_structure_loads(self):
        data = {
            "llm": {
                "active_profile": "p/m",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {
                        "id": "p/m",
                        "base_url": "https://p.example.com/v1",
                        "api_key": "p-secret",
                        "model": "m",
                        "temperature": 0.5,
                        "max_tokens": 2000,
                        "context_window": 16000,
                    }
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.llm["active_profile"], "p/m")
        self.assertEqual(config.model, "m")
        self.assertEqual(config.api_key, "p-secret")
        self.assertEqual(config.temperature, 0.5)

    def test_save_writes_profiles_when_profiles_exist(self):
        data = {
            "llm": {
                "active_profile": "p/m",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {
                        "id": "p/m",
                        "base_url": "https://p.example.com/v1",
                        "api_key": "p-secret",
                        "model": "m",
                    }
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config = Config(config_path=str(self.config_path))
        config.workspace_root = str(self.temp_dir.name)
        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertIn("profiles", saved["llm"])
        self.assertNotIn("providers", saved["llm"])
        self.assertEqual(saved["llm"]["profiles"][0]["api_key"], "p-secret")


class TestConfigDraftProfiles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        data = {
            "llm": {
                "active_profile": "p1/m1",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {
                        "id": "p1/m1",
                        "base_url": "https://p1.example.com/v1",
                        "api_key": "secret1",
                        "model": "m1",
                    }
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config = Config(config_path=str(self.config_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_profile(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.add_profile(
            id="p2/m2", base_url="https://p2.example.com/v1", api_key="secret2", model="m2"
        ))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(len(self.config.llm["profiles"]), 2)

    def test_set_and_clear_key(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.set_key("p1/m1", "new-secret"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self.config.llm["profiles"][0]["api_key"], "new-secret")

        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.clear_key("p1/m1"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self.config.llm["profiles"][0]["api_key"], "")

    def test_roles(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.set_role("chat", "p1/m1"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self.config.model_roles["chat"], "p1/m1")

        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.clear_role("chat"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertNotIn("chat", self.config.model_roles)

    def test_workspace_bookmarks(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.add_workspace_bookmark("home", "/home/user"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(len(self.config.workspace_bookmarks), 1)

        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.remove_workspace_bookmark("home"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(len(self.config.workspace_bookmarks), 0)

    def test_export_redacts_keys(self):
        draft = ConfigDraft.from_config(self.config)
        data = draft.export_config(with_keys=False)
        self.assertEqual(data["llm"]["profiles"][0]["api_key"], "")

    def test_export_with_keys(self):
        draft = ConfigDraft.from_config(self.config)
        data = draft.export_config(with_keys=True)
        self.assertEqual(data["llm"]["profiles"][0]["api_key"], "secret1")


class TestRuntimeCommands025(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        data = {
            "llm": {
                "active_profile": "p1/m1",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {"id": "p1/m1", "base_url": "https://p1.example.com/v1", "api_key": "secret1", "model": "m1"},
                    {"id": "p2/m2", "base_url": "https://p2.example.com/v1", "api_key": "", "model": "m2"},
                ],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config = Config(config_path=str(self.config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("agent.runtime_commands.ask", return_value="new-secret")
    @patch("agent.runtime_commands.confirm", return_value=True)
    def test_key_set(self, mock_confirm, mock_ask):
        result = handle_key_set(self.agent, "", ["/key", "set", "p2/m2"])
        self.assertTrue(result.success)
        self.assertEqual(self.config.llm["profiles"][1]["api_key"], "new-secret")

    @patch("agent.runtime_commands.confirm", return_value=True)
    def test_key_clear(self, mock_confirm):
        result = handle_key_clear(self.agent, "", ["/key", "clear", "p1/m1"])
        self.assertTrue(result.success)
        self.assertEqual(self.config.llm["profiles"][0]["api_key"], "")

    @patch("agent.runtime_commands.confirm", return_value=True)
    def test_key_reveal(self, mock_confirm):
        result = handle_key_reveal(self.agent, "", ["/key", "reveal", "p1/m1"])
        self.assertTrue(result.success)
        self.assertIn("secret1", result.message)

    def test_keys_masks(self):
        result = handle_keys(self.agent, "", [])
        self.assertTrue(result.success)
        self.assertNotIn("secret1", result.message)
        self.assertIn("********", result.message)

    def test_roles(self):
        result = handle_role_set(self.agent, "", ["/role", "set", "chat p2/m2"])
        self.assertTrue(result.success)
        self.assertEqual(self.config.model_roles["chat"], "p2/m2")

        result = handle_roles(self.agent, "", [])
        self.assertIn("chat: p2/m2", result.message)

        result = handle_role_clear(self.agent, "", ["/role", "clear", "chat"])
        self.assertTrue(result.success)
        self.assertNotIn("chat", self.config.model_roles)

    def test_workspace_save_and_remove(self):
        result = handle_workspace_save(self.agent, "", ["/workspace", "save", "home"])
        self.assertTrue(result.success)
        self.assertEqual(self.config.workspace_bookmarks[0]["name"], "home")

        result = handle_workspaces(self.agent, "", [])
        self.assertTrue(result.success)
        self.assertIn("home", result.message)

        result = handle_workspace_remove(self.agent, "", ["/workspace", "remove", "home"])
        self.assertTrue(result.success)
        self.assertEqual(len(self.config.workspace_bookmarks), 0)

    def test_resolve_workspace_target(self):
        self.config.workspace_bookmarks = [{"name": "home", "path": "/home/user"}]
        self.assertEqual(_resolve_workspace_target(self.config, "home"), "/home/user")
        self.assertEqual(_resolve_workspace_target(self.config, "/tmp"), "/tmp")

    @patch("agent.runtime_commands.datetime")
    def test_config_export(self, mock_datetime):
        mock_datetime.now.return_value.strftime = lambda fmt: "20260102-030405"
        result = handle_config_export(self.agent, "", ["/config", "export"])
        self.assertTrue(result.success)
        export_path = Path(self.temp_dir.name) / ".kairo" / "config_exports" / "config.export.20260102-030405.json"
        self.assertTrue(export_path.exists())
        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["llm"]["profiles"][0]["api_key"], "")

    def test_config_export_refuses_import_redacted(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            export_path = Path(f.name)
            json.dump({"llm": {"profiles": [{"id": "p/m", "base_url": "https://p.example.com/v1", "api_key": mask_key("secret1"), "model": "m"}]}}, f)
        try:
            draft = ConfigDraft.from_config(self.agent.config)
            report = draft.import_config(str(export_path))
            self.assertFalse(report.ok)
        finally:
            export_path.unlink(missing_ok=True)

    @patch("agent.runtime_commands.test_connection")
    def test_doctor_does_not_leak_keys(self, mock_test):
        from agent.provider_health import ProviderTestResult
        mock_test.return_value = ProviderTestResult(status="success", http_status=200, provider_message="ok", elapsed_ms=1)
        result = handle_doctor(self.agent, "", [])
        self.assertNotIn("secret1", result.message)


class TestCommandDispatcher025(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        data = {
            "llm": {
                "active_profile": "p/m",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [{"id": "p/m", "base_url": "https://p.example.com/v1", "api_key": "", "model": "m"}],
            },
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config = Config(config_path=str(self.config_path))
        self.registry = ToolRegistry()
        self.agent = Agent(config=self.config, registry=self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keys_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/keys")
        self.assertTrue(result.handled)

    @patch("agent.runtime_commands.confirm", return_value=True)
    @patch("agent.runtime_commands.ask", return_value="new-secret")
    def test_key_set_command(self, mock_ask, mock_confirm):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/key set p/m")
        self.assertTrue(result.handled)

    def test_roles_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/roles")
        self.assertTrue(result.handled)

    def test_role_set_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/role set chat p/m")
        self.assertTrue(result.handled)
        self.assertFalse(result.success)
        self.assertEqual(result.data.get("kind"), "removed_command")

    def test_doctor_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/doctor")
        self.assertTrue(result.handled)

    def test_workspace_save_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/workspace save home")
        self.assertTrue(result.handled)
        self.assertFalse(result.success)
        self.assertEqual(result.data.get("kind"), "removed_command")

    def test_workspaces_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/workspaces")
        self.assertTrue(result.handled)

    def test_config_export_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/config export")
        self.assertTrue(result.handled)

    def test_session_search_command(self):
        dispatcher = CommandDispatcher(self.agent)
        result = dispatcher.dispatch("/session search hello")
        self.assertTrue(result.handled)


if __name__ == "__main__":
    unittest.main()
