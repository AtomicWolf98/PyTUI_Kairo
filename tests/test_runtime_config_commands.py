"""Tests for the 0.2.3 runtime config / session command handlers.

These exercise the dispatch layer with simulated stdin input and verify the
draft UX (validate, backup, restore, add/edit/remove) plus session rename /
delete / export / reveal flows.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from agent.commands import CommandDispatcher
from agent.config import Config
from agent.context_manager import ConversationManager
from agent.session_store import SessionStore


def _seed_config(path: Path) -> Dict[str, Any]:
    data = {
        "llm": {
            "active_provider": "alpha",
            "active_model": "alpha-1",
            "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
            "providers": [
                {
                    "name": "alpha",
                    "base_url": "https://alpha.example.com/v1",
                    "api_key_env": "KAIRO_ALPHA_KEY",
                    "models": [
                        {"name": "alpha-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}
                    ],
                },
                {
                    "name": "beta",
                    "base_url": "https://beta.example.com/v1",
                    "api_key_env": "KAIRO_BETA_KEY",
                    "models": [
                        {"name": "beta-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}
                    ],
                },
            ],
        },
        "sessions": {"enabled": True, "storage_dir": ".kairo/sessions"},
        "workspace_root": ".",
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return data


class _TempProject:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        # Use a session directory alongside the config file so we can verify persistence.
        self.config_path = self.root / "config.json"
        _seed_config(self.config_path)

    def cleanup(self):
        self.dir.cleanup()


class _FakeAgent:
    """A minimal agent shim that satisfies CommandDispatcher and the runtime handlers."""

    def __init__(self, config: Config, sessions_root: Path):
        self.config = config
        self.config.config_path = Path(config.config_path)
        # SessionStore needs a *real* directory; create one under the temp root.
        self.config.sessions["storage_dir"] = str(sessions_root / "sessions")
        session_store = SessionStore(str(self.config.sessions["storage_dir"]), str(self.config.config_path))
        session_store._storage_dir.mkdir(parents=True, exist_ok=True)
        self.conversations = ConversationManager(
            system_instruction="system",
            context_window=config.context_window,
            session_store=session_store,
            workspace_root=str(sessions_root),
            model_profile=config.active_model_profile,
        )
        # Seed one extra session so deletion tests can protect the last one.
        self.conversations.create_session("First Aux")

    # Mimic Agent.handle_command entry path for nested calls made by handlers.
    def handle_command(self, raw: str):
        dispatcher = CommandDispatcher(self)
        # Strip and dispatch; ignore the message output for shim callers.
        return dispatcher.dispatch(raw)


class TestRuntimeConfigCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempProject()
        self.config = Config(config_path=str(self.tmp.config_path))
        self.agent = _FakeAgent(self.config, sessions_root=self.tmp.root)
        self.dispatcher = CommandDispatcher(self.agent)

    def tearDown(self):
        self.tmp.cleanup()

    def _dispatch_with_input(self, raw: str, fake_input_lines: List[str]) -> Any:
        """Run a command handler while feeding fake_input_lines to builtins.input."""
        buffer = io.StringIO()
        responses = list(fake_input_lines)

        def fake_input(prompt=""):
            nonlocal responses
            return responses.pop(0) if responses else ""

        with redirect_stdout(buffer):
            with patch("builtins.input", fake_input):
                result = self.dispatcher.dispatch(raw)
        self.last_buffer = buffer.getvalue()
        return result

    def test_providers_lists_active_provider(self):
        result = self._dispatch_with_input("/providers", [])
        self.assertTrue(result.handled)
        self.assertEqual(result.data.get("kind"), "providers")
        # Both seeded providers should appear in the printed output.
        self.assertIn("alpha", self.last_buffer)
        self.assertIn("beta", self.last_buffer)

    def test_provider_add_via_wizard(self):
        # Inputs align with handle_provider_add prompts in order.
        lines = [
            "gamma",                              # provider name
            "https://gamma.example.com/v1",       # base URL
            "env",                                # api key mode
            "KAIRO_GAMMA_KEY",                    # env name
            "gamma-1",                            # model name
            "16000",                              # context window
            "2000",                               # max tokens
            "0.3",                                # temperature
            "n",                                  # test connection now? No.
            "y",                                  # save and switch? Yes.
        ]
        result = self._dispatch_with_input("/provider add", lines)
        self.assertTrue(result.handled)
        self.assertTrue(result.success, self.last_buffer)
        self.assertEqual(result.data.get("kind"), "provider_saved")
        with open(self.tmp.config_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        names = [p["name"] for p in saved["llm"]["providers"]]
        self.assertIn("gamma", names)
        self.assertEqual(saved["llm"]["active_provider"], "gamma")
        self.assertEqual(saved["llm"]["active_model"], "gamma-1")

    def test_provider_add_requires_name(self):
        result = self._dispatch_with_input("/provider add", [""])
        self.assertTrue(result.handled)
        self.assertFalse(result.success)

    def test_provider_remove_last_provider_refused(self):
        # Strip down to one provider first by dispatching remove twice with confirms.
        # alpha + beta: remove beta.
        result = self._dispatch_with_input(
            "/provider remove",
            ["0", "y"],  # select provider index 0 (alpha), confirm
        )
        self.assertTrue(result.success)
        result = self._dispatch_with_input(
            "/provider remove",
            ["y"],  # only one remains, attempt to remove it
        )
        self.assertFalse(result.success)
        self.assertIn("Cannot remove the last", result.message)

    def test_model_add_to_provider(self):
        lines = [
            "0",            # choose alpha provider
            "alpha-2",      # model name
            "16000",        # context
            "2000",         # max tokens
            "0.4",          # temperature
            "y",            # save
        ]
        result = self._dispatch_with_input("/model add", lines)
        self.assertTrue(result.success, self.last_buffer)
        with open(self.tmp.config_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        alpha_models = next(p for p in saved["llm"]["providers"] if p["name"] == "alpha")["models"]
        self.assertIn("alpha-2", [m["name"] for m in alpha_models])

    def test_model_remove_protects_last_model(self):
        # beta has one model; trying to remove it should fail.
        lines = ["1", "0", "y"]  # provider=beta, model=index 0, confirm
        result = self._dispatch_with_input("/model remove", lines)
        self.assertFalse(result.success)
        self.assertIn("only one model", result.message)

    def test_config_validate_passes(self):
        result = self._dispatch_with_input("/config validate", [])
        self.assertTrue(result.success)
        self.assertEqual(result.data.get("kind"), "config_validate")

    def test_config_backup_creates_file(self):
        result = self._dispatch_with_input("/config backup", [])
        self.assertTrue(result.success)
        backups = Config.list_backups(self.tmp.config_path)
        self.assertGreaterEqual(len(backups), 1)

    def test_config_restore_round_trip(self):
        # Make a backup, then mutate config, then restore.
        self._dispatch_with_input("/config backup", [])
        # Mutate config on disk to a different workspace.
        with open(self.tmp.config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["workspace_root"] = "/elsewhere"
        with open(self.tmp.config_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        backups = Config.list_backups(self.tmp.config_path)
        self.assertGreaterEqual(len(backups), 1)
        # Pick the newest backup (index 0).
        result = self._dispatch_with_input("/config restore", ["0", "y"])
        self.assertTrue(result.success)
        with open(self.tmp.config_path, "r", encoding="utf-8") as handle:
            restored = json.load(handle)
        self.assertEqual(restored["workspace_root"], ".")

    def test_docs_lists_topics(self):
        result = self._dispatch_with_input("/docs", [])
        self.assertTrue(result.success)
        self.assertIn("docs/", self.last_buffer)

    def test_session_rename_updates_name(self):
        # Select nothing from selection (rename doesn't list), enter new name.
        lines = ["My Renamed"]
        result = self._dispatch_with_input("/session rename", lines)
        self.assertTrue(result.success, self.last_buffer)
        # Active session name on the in-memory session should match.
        self.assertEqual(self.agent.conversations.active.name, "My Renamed")

    def test_session_reveal_returns_path(self):
        result = self._dispatch_with_input("/session reveal", [])
        self.assertTrue(result.success)
        self.assertTrue(Path(result.data["path"]).exists())

    def test_session_export_writes_markdown(self):
        result = self._dispatch_with_input("/session export", ["markdown"])
        self.assertTrue(result.success)
        exported_path = Path(result.data["path"])
        self.assertTrue(exported_path.exists())
        content = exported_path.read_text(encoding="utf-8")
        self.assertIn("# ", content)

    def test_session_delete_protects_last_session(self):
        # Force one active session only by deleting extra sessions quickly:
        # easier path: set conversations.sessions to a single entry.
        self.agent.conversations.sessions = [self.agent.conversations.active]
        result = self._dispatch_with_input("/session delete", ["0", "y"])
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()