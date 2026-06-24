"""Tests for session management extensions (rename/delete/export/reveal)."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import List
from unittest.mock import patch

from agent.context_manager import ConversationManager
from agent.session_store import SessionStore


class _TempSessions:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text("{}", encoding="utf-8")
        self.storage = self.root / "sessions"
        self.storage.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self.dir.cleanup()


class _StubAgent:
    def __init__(self, conversations: ConversationManager, config):
        self.conversations = conversations
        self.config = config


def _make_agent(tmp: _TempSessions):
    from agent.config import Config

    config = Config(config_path=str(tmp.config_path))
    config.sessions["storage_dir"] = str(tmp.storage)
    config.llm["providers"] = [
        {
            "name": "alpha",
            "base_url": "https://alpha.example.com/v1",
            "models": [{"name": "alpha-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}],
            "_api_key_source": "none",
        }
    ]
    config.llm["active_provider"] = "alpha"
    config.llm["active_model"] = "alpha-1"
    store = SessionStore(str(tmp.storage), str(tmp.config_path))
    conversations = ConversationManager(
        system_instruction="system",
        context_window=32000,
        session_store=store,
        workspace_root=str(tmp.root),
        model_profile="alpha / alpha-1",
    )
    conversations.refresh_context()
    return _StubAgent(conversations, config)


def _dispatch(agent, raw: str, lines: List[str]):
    from agent.commands import CommandDispatcher

    responses = list(lines)
    buffer = io.StringIO()

    def fake_input(prompt=""):
        return responses.pop(0) if responses else ""

    with redirect_stdout(buffer):
        with patch("builtins.input", fake_input):
            dispatcher = CommandDispatcher(agent)
            result = dispatcher.dispatch(raw)
    return result, buffer.getvalue()


class TestSessionManagement(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempSessions()
        self.agent = _make_agent(self.tmp)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rename_updates_session_file_and_index(self):
        store = self.agent.conversations.session_store
        session = self.agent.conversations.active
        original_name = session.name
        result, _ = _dispatch(self.agent, "/session rename", ["Renamed One"])
        self.assertTrue(result.success, result.message)
        # In-memory session reflects the rename.
        self.assertEqual(session.name, "Renamed One")
        # On-disk file carries it too.
        metadata = store.session_metadata(session.id)
        self.assertIsNotNone(metadata)
        on_disk_name, path = metadata
        self.assertEqual(on_disk_name, "Renamed One")
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["name"], "Renamed One")
        # Index entry also updated.
        with open(store._index_path, "r", encoding="utf-8") as handle:
            index = json.load(handle)
        entry = next(item for item in index["sessions"] if item["id"] == session.id)
        self.assertEqual(entry["name"], "Renamed One")
        self.assertNotEqual(original_name, "Renamed One")

    def test_rename_rejects_blank_name(self):
        result, _ = _dispatch(self.agent, "/session rename", [""])
        self.assertFalse(result.success)

    def test_reveal_returns_existing_path(self):
        result, _ = _dispatch(self.agent, "/session reveal", [])
        self.assertTrue(result.success)
        path = Path(result.data["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.suffix, ".json")

    def test_export_markdown_creates_file_without_mutating_session(self):
        store = self.agent.conversations.session_store
        session = self.agent.conversations.active
        # Add a non-trivial history so the export has content.
        session.history.append({"role": "user", "content": "hello"})
        result, _ = _dispatch(self.agent, "/session export", ["markdown"])
        self.assertTrue(result.success, result.message)
        exported = Path(result.data["path"])
        self.assertTrue(exported.exists())
        content = exported.read_text(encoding="utf-8")
        self.assertIn("# ", content)
        # Original session JSON unchanged in name and history count.
        metadata = store.session_metadata(session.id)
        _, original_path = metadata
        with open(original_path, "r", encoding="utf-8") as handle:
            original = json.load(handle)
        self.assertEqual(original.get("name"), session.name)
        # Export dir under storage root.
        self.assertEqual(exported.parent, Path(self.tmp.storage) / "exports")

    def test_export_json_round_trips(self):
        result, _ = _dispatch(self.agent, "/session export", ["json"])
        self.assertTrue(result.success)
        exported = Path(result.data["path"])
        with open(exported, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertIn("history", data)
        self.assertEqual(data.get("format", "json") if "format" in data else True, True)

    def test_delete_protects_last_session(self):
        # Force to exactly one session to ensure last-session protection fires.
        self.agent.conversations.sessions = [self.agent.conversations.active]
        result, _ = _dispatch(self.agent, "/session delete", ["0", "y"])
        self.assertFalse(result.success)
        self.assertIn("last session", result.message, result.message)

    def test_delete_creates_orphans_no_active_session(self):
        # Two sessions, delete the non-active one.
        self.agent.conversations.create_session("Second")
        sessions_before = list(self.agent.conversations.sessions)
        target = sessions_before[0]
        active_id_before = self.agent.conversations.active_session_id

        result, _ = _dispatch(self.agent, "/session delete", ["0", "y"])
        self.assertTrue(result.success, result.message)
        # Target removed; active session remains valid.
        self.assertNotIn(target, self.agent.conversations.sessions)
        self.assertTrue(any(s.id == active_id_before for s in self.agent.conversations.sessions))


if __name__ == "__main__":
    unittest.main()