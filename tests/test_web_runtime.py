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

    def test_graphical_settings_api_masks_keys_and_updates_sections(self):
        view = self.client.get("/api/settings/view?token=test-token")
        self.assertEqual(view.status_code, 200)
        payload = view.json()
        self.assertIn("general", payload)
        self.assertIn("providers", payload)
        self.assertEqual(payload["diagnostics"]["backend_version"], "0.3.2-preview")
        self.assertEqual(payload["diagnostics"]["static_version"], "0.3.2-preview")
        self.assertTrue(payload["diagnostics"]["version_match"])
        self.assertNotIn("secret-key", json.dumps(payload))

        general = self.client.patch(
            "/api/settings/general?token=test-token",
            json={"language": "zh-CN", "shell_type": "powershell", "authorization_level": "auto", "plan_mode": True},
        )
        self.assertEqual(general.status_code, 200)
        self.assertEqual(self.config.shell_type, "powershell")
        self.assertEqual(self.config.authorization_level, "auto")
        self.assertTrue(self.config.plan_mode)
        self.assertEqual(self.config._extra_fields["appearance"]["language"], "zh-CN")
        self.assertEqual(self.runtime.agent.registry.tools["run_command"].session.shell_type, "powershell")

        user = self.client.patch(
            "/api/settings/user?token=test-token",
            json={"name": "Tester", "timezone": "Asia/Shanghai", "preferences": "quiet UI"},
        )
        self.assertEqual(user.status_code, 200)
        self.assertEqual(self.config._extra_fields["user"]["name"], "Tester")
        self.assertIn("Preferences: quiet UI", self.runtime.agent.system_instruction)

        assistant = self.client.patch(
            "/api/settings/assistant?token=test-token",
            json={"name": "Kai Custom", "system_prompt": "Prefer precise, calm answers.", "thinking_mode": True, "context_management": {"trigger_percent": 80}},
        )
        self.assertEqual(assistant.status_code, 200)
        self.assertTrue(self.config.thinking_mode)
        self.assertEqual(self.config.context_management_defaults["trigger_percent"], 80)
        self.assertIn("Assistant display name: Kai Custom", self.runtime.agent.system_instruction)
        self.assertIn("Prefer precise, calm answers.", self.runtime.agent.history[0]["content"])

        roles = self.client.patch(
            "/api/settings/roles?token=test-token",
            json={"chat": "test/test-model", "fast": "test/test-model"},
        )
        self.assertEqual(roles.status_code, 200)
        self.assertEqual(self.config.model_roles["fast"], "test/test-model")

        appearance = self.client.patch(
            "/api/settings/appearance?token=test-token",
            json={
                "theme": "kairo-light",
                "density": "compact",
                "font_size": 17,
                "animation": "reduced",
                "mascot": False,
                "reduced_motion": True,
            },
        )
        self.assertEqual(appearance.status_code, 200)
        self.assertEqual(self.config.web["theme"], "kairo-light")
        self.assertEqual(self.config._extra_fields["appearance"]["density"], "compact")
        self.assertEqual(appearance.json()["settings"]["appearance"]["font_size"], 17)

        new_root = self.root / "next_workspace"
        new_root.mkdir()
        (new_root / "custom_skills").mkdir()
        reload_calls = []

        def fake_reload(skills_dir, *, require_hash=False, workspace_root=None):
            reload_calls.append({
                "skills_dir": skills_dir,
                "require_hash": require_hash,
                "workspace_root": str(workspace_root),
            })

        self.runtime.agent.registry.reload_custom_skills = fake_reload
        workbench = self.client.patch(
            "/api/settings/workbench?token=test-token",
            json={
                "workspace_root": str(new_root),
                "skills_dir": "custom_skills",
                "shell_type": "powershell",
                "workspace_max_files": 99,
                "workspace_diff_max_bytes": 4096,
                "workspace_refresh_seconds": 1.5,
                "workspace_bookmarks": [{"name": "next", "path": str(new_root)}],
            },
        )
        self.assertEqual(workbench.status_code, 200)
        self.assertEqual(Path(self.config.workspace_root).resolve(), new_root.resolve())
        self.assertEqual(self.runtime.agent.workspace_context.root, new_root.resolve())
        self.assertEqual(self.runtime.workspace.monitor.root, new_root.resolve())
        self.assertEqual(self.config.ui["workspace_max_files"], 99)
        self.assertTrue(any(call["skills_dir"] == "custom_skills" for call in reload_calls))

        skills = self.client.patch(
            "/api/settings/skills?token=test-token",
            json={"skills_dir": "custom_skills", "require_hash": True},
        )
        self.assertEqual(skills.status_code, 200)
        self.assertTrue(self.config.policy["skills"]["require_hash"])
        self.assertTrue(any(call["require_hash"] for call in reload_calls))

    def test_provider_and_profile_settings_api(self):
        created_provider = self.client.post(
            "/api/settings/provider?token=test-token",
            json={
                "id": "extra",
                "base_url": "https://extra.test/v1",
                "model": "extra-model",
                "api_key": "extra-secret",
                "context_window": 64000,
            },
        )
        self.assertEqual(created_provider.status_code, 200)
        self.assertNotIn("extra-secret", json.dumps(created_provider.json()))
        self.assertTrue(any(profile.get("provider") == "extra" for profile in self.config.llm["profiles"]))

        tested = self.client.post("/api/settings/provider/extra/test?token=test-token")
        self.assertEqual(tested.status_code, 200)
        self.assertTrue(tested.json()["ok"])

        updated_provider = self.client.patch(
            "/api/settings/provider/extra?token=test-token",
            json={"base_url": "https://extra2.test/v1", "api_key": "", "api_key_env": "EXTRA_KEY"},
        )
        self.assertEqual(updated_provider.status_code, 200)
        extra_profile = next(profile for profile in self.config.llm["profiles"] if profile.get("provider") == "extra")
        self.assertEqual(extra_profile["base_url"], "https://extra2.test/v1")
        self.assertEqual(extra_profile["api_key"], "extra-secret")
        self.assertEqual(extra_profile["api_key_env"], "EXTRA_KEY")

        created_profile = self.client.post(
            "/api/settings/profile?token=test-token",
            json={
                "id": "test/second",
                "label": "Second",
                "provider": "test",
                "base_url": "https://example.test/v1",
                "model": "second",
                "temperature": 0.1,
                "max_tokens": 123,
                "context_window": 456,
            },
        )
        self.assertEqual(created_profile.status_code, 200)
        self.assertTrue(any(profile.get("id") == "test/second" for profile in self.config.llm["profiles"]))

        updated_profile = self.client.patch(
            "/api/settings/profile/test%2Fsecond?token=test-token",
            json={
                "label": "Second Updated",
                "provider": "test",
                "base_url": "https://example.test/v1",
                "model": "second",
                "temperature": 0.3,
                "max_tokens": 321,
                "context_window": 654,
            },
        )
        self.assertEqual(updated_profile.status_code, 200)
        second = next(profile for profile in self.config.llm["profiles"] if profile.get("id") == "test/second")
        self.assertEqual(second["label"], "Second Updated")
        self.assertEqual(second["max_tokens"], 321)

        deleted_profile = self.client.delete("/api/settings/profile/test%2Fsecond?token=test-token")
        self.assertEqual(deleted_profile.status_code, 200)
        self.assertFalse(any(profile.get("id") == "test/second" for profile in self.config.llm["profiles"]))

        deleted_provider = self.client.delete("/api/settings/provider/extra?token=test-token")
        self.assertEqual(deleted_provider.status_code, 200)
        self.assertFalse(any(profile.get("provider") == "extra" for profile in self.config.llm["profiles"]))
