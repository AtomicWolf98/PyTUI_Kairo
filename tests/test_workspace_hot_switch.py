import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.bootstrap import build_agent
from agent.config import Config
from agent.context_manager import RUNTIME_STATE_NAME
from agent.workspace_context import WorkspaceMoveError
from tools.patch_ops import SearchFileTool


class TestWorkspaceMoveHotSwitch(unittest.TestCase):
    def _make_temp_config(self):
        data = {
            "llm": {
                "active_provider": "test",
                "active_model": "test-model",
                "defaults": {
                    "temperature": 0.2,
                    "max_tokens": 4000,
                    "context_window": 128000,
                },
                "providers": [
                    {
                        "name": "test",
                        "base_url": "https://test.api.com/v1",
                        "models": [
                            {
                                "name": "test-model",
                                "temperature": 0.2,
                                "max_tokens": 4000,
                                "context_window": 128000,
                            }
                        ],
                    }
                ],
            },
            "ui": {
                "mode": "auto",
                "theme": "kairo-dark",
                "animation": "full",
                "mascot": True,
                "dock_breakpoint": 120,
                "dock_width_ratio": 0.333,
                "dock_min_width": 36,
                "dock_max_width": 64,
                "reduced_motion": False,
                "workspace_enabled": False,
            },
            "workspace_root": str(self.old),
            "skills_dir": "./skills",
            "shell_type": "cmd",
            "authorization_level": "auto",
            "plan_mode": False,
            "thinking_mode": False,
            "sessions": {"enabled": False},
        }
        path = self.root / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.old.mkdir()
        self.new.mkdir()

        config_path = self._make_temp_config()
        self.config = Config(config_path)
        self.agent = build_agent(self.config)

    def tearDown(self):
        # Close persistent shell/Python sessions so temporary dirs can be removed.
        try:
            self.agent.shutdown()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_move_workspace_updates_runtime_state(self):
        result = self.agent.move_workspace(self.new)
        self.assertTrue(result.success)

        runtime_message = self.agent.history[1]
        self.assertEqual(runtime_message.get("name"), RUNTIME_STATE_NAME)
        self.assertIn(str(self.new), runtime_message.get("content", ""))
        self.assertNotIn(str(self.old), runtime_message.get("content", ""))

    def test_move_workspace_updates_config_and_context_root(self):
        result = self.agent.move_workspace(self.new)
        self.assertTrue(result.success)
        self.assertEqual(Path(self.config.workspace_root).resolve(), self.new.resolve())
        self.assertEqual(self.agent.workspace_context.root.resolve(), self.new.resolve())

    def test_move_workspace_resets_python_repl(self):
        # Set a variable in the old workspace REPL.
        self.agent.registry.tools["run_python_code"].repl.execute("workspace_marker = 'old'")

        result = self.agent.move_workspace(self.new)
        self.assertTrue(result.success)

        output = self.agent.registry.tools["run_python_code"].repl.execute("'workspace_marker' in globals()")
        self.assertIn("False", output)

    def test_move_workspace_reloads_custom_skills(self):
        # Create a skill in the new workspace.
        new_skills = self.new / "skills"
        new_skills.mkdir()
        (new_skills / "new_skill.py").write_text(
            "from tools.base import skill\n"
            "@skill(name='new_skill', description='From new workspace')\n"
            "def new_skill():\n"
            "    return 'new workspace result'\n",
            encoding="utf-8",
        )

        result = self.agent.move_workspace(self.new)
        self.assertTrue(result.success)
        self.assertIn("new_skill", self.agent.registry.tools)
        self.assertEqual(self.agent.registry.tools["new_skill"].execute(), "new workspace result")

    def test_move_workspace_unloads_old_skills(self):
        # Create a skill in the old workspace and reload on the existing agent.
        # build_agent already created ./skills relative to the old workspace.
        old_skills = self.old / "skills"
        (old_skills / "old_skill.py").write_text(
            "from tools.base import skill\n"
            "@skill(name='old_skill', description='From old workspace')\n"
            "def old_skill():\n"
            "    return 'old workspace result'\n",
            encoding="utf-8",
        )
        self.agent.registry.reload_custom_skills(
            self.config.skills_dir,
            workspace_root=self.old,
        )
        self.assertIn("old_skill", self.agent.registry.tools)

        result = self.agent.move_workspace(self.new)
        self.assertTrue(result.success)
        self.assertNotIn("old_skill", self.agent.registry.tools)

    def test_move_workspace_failure_returns_failed_kind(self):
        with mock.patch.object(
            self.agent.workspace_context,
            "move",
            side_effect=WorkspaceMoveError("boom"),
        ):
            result = self.agent.move_workspace(self.new)

        self.assertFalse(result.success)
        self.assertEqual(result.data["kind"], "workspace_move_failed")

    def test_search_file_uses_policy_root_for_relative_paths(self):
        (self.old / "target.txt").write_text("find me here", encoding="utf-8")

        search_tool = SearchFileTool(config=self.config, workspace_context=self.agent.workspace_context)
        result = search_tool.execute(query="find me", path=".")
        self.assertIn("target.txt", result)
        self.assertNotIn("..", result.split(":")[0])

        # Move workspace and search again.
        self.agent.move_workspace(self.new)
        (self.new / "target.txt").write_text("find me there", encoding="utf-8")
        result = search_tool.execute(query="find me", path=".")
        self.assertIn("target.txt", result)
        self.assertNotIn(str(self.old.name), result.split(":")[0])


if __name__ == "__main__":
    unittest.main()
