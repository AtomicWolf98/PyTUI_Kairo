"""Tests for Kairo 0.2.6-beta: provider API key preservation on edit."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.config_editor import ConfigDraft, KEY_CLEAR


def _three_provider_data(workspace_root: str) -> dict:
    return {
        "llm": {
            "active_provider": "alpha",
            "active_model": "alpha-1",
            "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
            "providers": [
                {
                    "name": "alpha",
                    "base_url": "https://alpha.example.com/v1",
                    "api_key": "sk-alpha",
                    "models": [{"name": "alpha-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}],
                },
                {
                    "name": "beta",
                    "base_url": "https://beta.example.com/v1",
                    "api_key": "sk-beta",
                    "models": [{"name": "beta-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}],
                },
                {
                    "name": "gamma",
                    "base_url": "https://gamma.example.com/v1",
                    "api_key": "sk-gamma",
                    "models": [{"name": "gamma-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}],
                },
            ],
        },
        "sessions": {"enabled": False},
        "workspace_root": workspace_root,
    }


class TestProviderKeyPreservation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(_three_provider_data(self.temp_dir.name), f)
        self.config = Config(config_path=str(self.config_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _saved_provider(self, name: str) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        return next(p for p in saved["llm"]["providers"] if p["name"] == name)

    def test_edit_one_provider_preserves_other_keys(self):
        draft = ConfigDraft.from_config(self.config)
        # Edit only alpha's base_url; leave its key blank (keep).
        draft.update_provider("alpha", base_url="https://alpha-new.example.com/v1", api_key="")
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_provider("alpha")["api_key"], "sk-alpha")
        self.assertEqual(self._saved_provider("beta")["api_key"], "sk-beta")
        self.assertEqual(self._saved_provider("gamma")["api_key"], "sk-gamma")

    def test_blank_key_keeps_existing(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key="")  # blank -> keep
        report = draft.apply_to(self.config, backup=True, allow_inline_key=False)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_provider("alpha")["api_key"], "sk-alpha")

    def test_explicit_clear_only_clears_target(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key=KEY_CLEAR)
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertNotIn("api_key", self._saved_provider("alpha"))
        self.assertEqual(self._saved_provider("beta")["api_key"], "sk-beta")
        self.assertEqual(self._saved_provider("gamma")["api_key"], "sk-gamma")

    def test_new_key_only_replaces_target(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key="sk-alpha-replaced")
        report = draft.apply_to(self.config, backup=True, allow_inline_key=True)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_provider("alpha")["api_key"], "sk-alpha-replaced")
        self.assertEqual(self._saved_provider("beta")["api_key"], "sk-beta")
        self.assertEqual(self._saved_provider("gamma")["api_key"], "sk-gamma")

    def test_keys_preserved_after_save_and_reload(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", base_url="https://alpha-new.example.com/v1", api_key="")
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        reloaded = Config(config_path=str(self.config_path))
        keys = {p["name"]: p.get("api_key", "") for p in reloaded.llm["providers"]}
        self.assertEqual(keys, {"alpha": "sk-alpha", "beta": "sk-beta", "gamma": "sk-gamma"})


class TestProfileKeyPreservation(unittest.TestCase):
    """Same preservation semantics for the llm.profiles[] structure."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        data = {
            "llm": {
                "active_profile": "alpha/a",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
                "profiles": [
                    {"id": "alpha/a", "base_url": "https://alpha.example.com/v1", "api_key": "sk-alpha", "model": "a"},
                    {"id": "beta/b", "base_url": "https://beta.example.com/v1", "api_key": "sk-beta", "model": "b"},
                ],
            },
            "sessions": {"enabled": False},
            "workspace_root": str(self.temp_dir.name),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.config = Config(config_path=str(self.config_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _saved_profile(self, pid: str) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        return next(p for p in saved["llm"]["profiles"] if p["id"] == pid)

    def test_edit_one_profile_preserves_other_key(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_profile("alpha/a", base_url="https://alpha-new.example.com/v1", api_key="")
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_profile("alpha/a")["api_key"], "sk-alpha")
        self.assertEqual(self._saved_profile("beta/b")["api_key"], "sk-beta")

    def test_explicit_clear_profile_key(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_profile("alpha/a", api_key=KEY_CLEAR)
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_profile("alpha/a")["api_key"], "")
        self.assertEqual(self._saved_profile("beta/b")["api_key"], "sk-beta")

    def test_clear_key_helper_clears_only_target(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.clear_key("alpha/a"))
        report = draft.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)
        self.assertEqual(self._saved_profile("alpha/a")["api_key"], "")
        self.assertEqual(self._saved_profile("beta/b")["api_key"], "sk-beta")


if __name__ == "__main__":
    unittest.main()
