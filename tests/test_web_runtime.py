import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent.config import Config
from agent.runtime import KairoRuntime
from agent.web import create_web_app


class TestWebRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sample.py").write_text("print('hello from web preview')\n", encoding="utf-8")
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps({
            "llm": {
                "active_profile": "test/test-model",
                "defaults": {"temperature": 0.2, "max_tokens": 100, "context_window": 10000},
                "profiles": [{
                    "id": "test/test-model",
                    "label": "Test Model",
                    "provider": "test",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret-key",
                    "model": "test-model",
                    "temperature": 0.2,
                    "max_tokens": 100,
                    "context_window": 10000,
                }],
            },
            "workspace_root": str(self.root),
            "workspace_bookmarks": [{"name": "root", "path": str(self.root)}],
            "skills_dir": "./skills",
            "sessions": {"enabled": False},
            "web": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 0,
                "open_browser": False,
                "theme": "system",
                "local_auth_token": True,
                "max_event_buffer": 1000,
            },
        }), encoding="utf-8")
        self.config = Config(str(config_path))
        self.runtime = KairoRuntime(self.config)
        self.client = TestClient(create_web_app(self.runtime, token="test-token"))

    def tearDown(self):
        self.runtime.shutdown()
        self.tmp.cleanup()

    def test_status_and_config_are_redacted(self):
        unauthorized = self.client.get("/api/status")
        self.assertEqual(unauthorized.status_code, 401)

        status = self.client.get("/api/status?token=test-token")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["profile"], "test / test-model")

        config = self.client.get("/api/config?token=test-token")
        self.assertEqual(config.status_code, 200)
        payload = json.dumps(config.json())
        self.assertNotIn("secret-key", payload)

    def test_workspace_sessions_skills_and_stop_endpoints(self):
        root = self.client.get("/?token=test-token")
        self.assertEqual(root.status_code, 200)
        self.assertIn("text/html", root.headers.get("content-type", ""))

        workspace = self.client.get("/api/workspace/snapshot?token=test-token")
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.json()["root"], str(self.root.resolve()))

        sessions = self.client.get("/api/sessions?token=test-token")
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(len(sessions.json()["sessions"]), 1)

        skills = self.client.get("/api/skills?token=test-token")
        self.assertEqual(skills.status_code, 200)
        self.assertTrue(skills.json()["tools"])

        stopped = self.client.post("/api/chat/stop?token=test-token")
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.json()["ok"])

    def test_config_web_update_persists(self):
        response = self.client.patch(
            "/api/config/web?token=test-token",
            json={"theme": "dark", "open_browser": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config.web["theme"], "dark")

    def test_preview_api_history_file_bookmarks_and_config_export(self):
        self.runtime.agent.history.append({"role": "user", "content": "hello"})
        self.runtime.agent.history.append({"role": "assistant", "content": "hi"})

        history = self.client.get("/api/chat/history?token=test-token")
        self.assertEqual(history.status_code, 200)
        self.assertEqual([item["role"] for item in history.json()["messages"]], ["user", "assistant"])

        preview = self.client.get("/api/workspace/file?path=sample.py&token=test-token")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["language"], "python")
        self.assertIn("hello from web preview", preview.json()["content"])

        traversal = self.client.get("/api/workspace/file?path=../secret.txt&token=test-token")
        self.assertEqual(traversal.status_code, 400)

        bookmarks = self.client.get("/api/workspace/bookmarks?token=test-token")
        self.assertEqual(bookmarks.status_code, 200)
        self.assertEqual(bookmarks.json()["bookmarks"][0]["name"], "root")

        added = self.client.post(
            "/api/workspace/bookmarks?token=test-token",
            json={"name": "src", "path": str(self.root)},
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(any(item["name"] == "src" for item in added.json()["bookmarks"]))

        removed = self.client.delete("/api/workspace/bookmarks/src?token=test-token")
        self.assertEqual(removed.status_code, 200)

        exported = self.client.post("/api/config/export?token=test-token", json={"with_keys": False})
        self.assertEqual(exported.status_code, 200)
        self.assertNotIn("secret-key", json.dumps(exported.json()))

        blocked = self.client.post("/api/config/export?token=test-token", json={"with_keys": True})
        self.assertEqual(blocked.status_code, 400)

        with_keys = self.client.post(
            "/api/config/export?token=test-token",
            json={"with_keys": True, "confirm": "EXPORT_KEYS"},
        )
        self.assertEqual(with_keys.status_code, 200)
        self.assertIn("secret-key", json.dumps(with_keys.json()))
