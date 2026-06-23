import json
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.context_manager import ConversationManager, RUNTIME_STATE_NAME
from agent.core import Agent
from agent.session_store import SessionStore
from agent.token_tracker import TokenTracker
from tools.base import ToolRegistry


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path.write_text("{}", encoding="utf-8")
        self.store = SessionStore(".kairo/sessions", str(self.config_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_session(self, name="Test"):
        session = self.store.create_session(name)
        session.history = [
            {"role": "system", "content": "You are Kairo."},
            {
                "role": "system",
                "name": RUNTIME_STATE_NAME,
                "content": "Kairo runtime state:\n- Current workspace root: /tmp\n- Active model profile: test / model\n- Authorization level: manual",
            },
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        session.token_tracker.add_tokens(10, 5)
        return session

    def test_storage_dir_resolves_relative_to_config(self):
        self.assertEqual(
            self.store.storage_dir,
            self.config_path.parent / ".kairo" / "sessions",
        )

    def test_save_session_creates_index_and_file(self):
        session = self._make_session()
        self.store.save_session(session, is_active=True)

        self.assertTrue(self.store.storage_dir.exists())
        self.assertTrue((self.store.storage_dir / "index.json").exists())
        self.assertTrue((self.store.storage_dir / f"{session.id}.json").exists())

        with open(self.store.storage_dir / "index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
        self.assertEqual(index["active_session_id"], session.id)
        self.assertEqual(len(index["sessions"]), 1)
        self.assertEqual(index["sessions"][0]["name"], "Test")

    def test_load_all_restores_session(self):
        session = self._make_session("Persistent")
        self.store.save_session(session, is_active=True)

        store2 = SessionStore(".kairo/sessions", str(self.config_path))
        sessions, active_id, warnings = store2.load_all(
            "You are Kairo.",
            workspace_root="/tmp",
            model_profile="test / model",
            authorization_level="manual",
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(active_id, session.id)
        restored = sessions[0]
        self.assertEqual(restored.name, "Persistent")
        self.assertEqual(len(restored.history), 4)
        self.assertEqual(restored.history[0]["content"], "You are Kairo.")
        self.assertEqual(restored.history[1].get("name"), RUNTIME_STATE_NAME)
        self.assertEqual(restored.token_tracker.session_input_tokens, 10)
        self.assertEqual(restored.token_tracker.session_output_tokens, 5)

    def test_load_all_updates_system_instruction(self):
        session = self._make_session()
        self.store.save_session(session, is_active=True)

        store2 = SessionStore(".kairo/sessions", str(self.config_path))
        sessions, _, _ = store2.load_all(
            "New system instruction.",
            workspace_root="/tmp",
            model_profile="test / model",
            authorization_level="manual",
        )
        self.assertEqual(sessions[0].history[0]["content"], "New system instruction.")
        # Rest of history preserved.
        self.assertEqual(sessions[0].history[2]["content"], "hello")

    def test_load_all_skips_corrupt_session_file(self):
        session = self._make_session("Good")
        self.store.save_session(session, is_active=True)

        bad_path = self.store.storage_dir / "bad.json"
        bad_path.write_text("this is not json", encoding="utf-8")
        # Add the corrupt file to the index so the loader attempts to read it.
        with open(self.store.storage_dir / "index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
        index["sessions"].append({
            "id": "bad",
            "name": "Bad",
            "file": "bad.json",
            "created_at": "2026-06-23T10:00:00Z",
            "updated_at": "2026-06-23T10:00:00Z",
        })
        with open(self.store.storage_dir / "index.json", "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        store2 = SessionStore(".kairo/sessions", str(self.config_path))
        sessions, active_id, warnings = store2.load_all(
            "You are Kairo.",
            workspace_root="/tmp",
            model_profile="test / model",
            authorization_level="manual",
        )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(active_id, session.id)
        self.assertTrue(any("bad" in w for w in warnings))

    def test_load_all_recovers_from_missing_index(self):
        session = self._make_session("Orphan")
        self.store.save_session(session, is_active=True)
        (self.store.storage_dir / "index.json").unlink()

        store2 = SessionStore(".kairo/sessions", str(self.config_path))
        sessions, active_id, warnings = store2.load_all(
            "You are Kairo.",
            workspace_root="/tmp",
            model_profile="test / model",
            authorization_level="manual",
        )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "Orphan")
        self.assertTrue(any("Recovered" in w for w in warnings))

    def test_atomic_write_does_not_leave_partial_file(self):
        session = self._make_session()
        self.store.save_session(session, is_active=True)

        tmp_files = list(self.store.storage_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_delete_session_removes_file_and_updates_index(self):
        session = self._make_session()
        self.store.save_session(session, is_active=True)

        self.assertTrue(self.store.delete_session(session.id))
        self.assertFalse((self.store.storage_dir / f"{session.id}.json").exists())

        with open(self.store.storage_dir / "index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
        self.assertEqual(index["sessions"], [])
        self.assertEqual(index["active_session_id"], "")


class TestConversationManagerWithSessionStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path.write_text("{}", encoding="utf-8")
        self.store = SessionStore("sessions", str(self.config_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manager_loads_sessions_from_store(self):
        session = self.store.create_session("Saved")
        session.history = [
            {"role": "system", "content": "You are Kairo."},
            {"role": "system", "name": RUNTIME_STATE_NAME, "content": "runtime"},
            {"role": "user", "content": "question"},
        ]
        self.store.save_session(session, is_active=True)

        manager = ConversationManager(
            "You are Kairo.",
            context_window=128000,
            session_store=self.store,
        )
        self.assertEqual(len(manager.sessions), 1)
        self.assertEqual(manager.active.name, "Saved")
        self.assertEqual(manager.active.history[2]["content"], "question")

    def test_save_active_persists_changes(self):
        manager = ConversationManager(
            "You are Kairo.",
            context_window=128000,
            session_store=self.store,
        )
        manager.append_message({"role": "user", "content": "hello"})
        manager.save_active(reason="test")

        manager2 = ConversationManager(
            "You are Kairo.",
            context_window=128000,
            session_store=self.store,
        )
        self.assertEqual(len(manager2.active.history), 3)
        self.assertEqual(manager2.active.history[-1]["content"], "hello")

    def test_update_runtime_state_replaces_message(self):
        manager = ConversationManager(
            "You are Kairo.",
            context_window=128000,
            session_store=self.store,
            workspace_root="/old",
        )
        self.assertIn("/old", str(manager.active.history[1]["content"]))

        manager.update_runtime_state(workspace_root="/new")
        self.assertIn("/new", str(manager.active.history[1]["content"]))
        self.assertNotIn("/old", str(manager.active.history[1]["content"]))
        # Only one runtime state message exists.
        self.assertEqual(
            sum(1 for m in manager.active.history if m.get("name") == RUNTIME_STATE_NAME),
            1,
        )


class TestAgentSessionPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config_path = self.root / "config.json"
        data = {
            "llm": {
                "active_provider": "test",
                "active_model": "test-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "providers": [
                    {
                        "name": "test",
                        "base_url": "https://test.api.com/v1",
                        "models": [{"name": "test-model", "temperature": 0.2, "max_tokens": 4000, "context_window": 128000}],
                    }
                ],
            },
            "workspace_root": str(self.workspace),
            "skills_dir": "./skills",
            "shell_type": "cmd",
            "authorization_level": "manual",
            "sessions": {"enabled": True, "storage_dir": "sessions"},
        }
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_agent_shutdown_saves_session(self):
        config = Config(str(self.config_path))
        agent = Agent(config, ToolRegistry())
        agent.conversations.create_session("Persist Me")
        agent.history.append({"role": "user", "content": "hello"})
        agent.shutdown()

        config2 = Config(str(self.config_path))
        config2.sessions["enabled"] = True
        agent2 = Agent(config2, ToolRegistry())
        self.assertEqual(agent2.active_session_name, "Persist Me")
        self.assertIn("hello", str(agent2.history))


if __name__ == "__main__":
    unittest.main()
