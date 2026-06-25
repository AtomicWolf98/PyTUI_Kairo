import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if importlib.util.find_spec("textual") is None:
    raise unittest.SkipTest("textual is not installed in the current test environment")

from agent.commands import COMMAND_CATALOG
from agent.config import Config
from agent.ui.app import KairoApp
from agent.workspace import WorkspaceSnapshot
from agent.ui.mascot import KAI_FRAMES, KAI_HEIGHT, KAI_WIDTH, KaiMascot
from agent.ui.widgets import (
    BrandHeader,
    CommandPalette,
    Composer,
    ConversationView,
    ExpandableToolOutput,
    ModeModal,
    ThoughtView,
    WorkspaceModal,
    WorkspacePanel,
    WorkspaceTree,
)
from tools.base import BaseTool, ToolRegistry


class TestKaiFrames(unittest.TestCase):
    def test_all_animation_frames_use_fixed_grid(self):
        expected_states = {
            "idle", "listening", "connecting", "thinking", "streaming",
            "tool_wait", "tool_run", "compressing", "success", "error",
        }
        self.assertEqual(set(KAI_FRAMES), expected_states)
        for frames in KAI_FRAMES.values():
            for frame in frames:
                lines = frame.splitlines()
                self.assertEqual(len(lines), KAI_HEIGHT)
                self.assertTrue(all(len(line) == KAI_WIDTH for line in lines))


class TestKairoApp(unittest.IsolatedAsyncioTestCase):
    def _make_temp_config(self):
        """Create a temporary config file so tests never mutate the repo config."""
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
                "workspace_enabled": True,
                "workspace_refresh_seconds": 2.0,
                "workspace_max_files": 2000,
                "workspace_diff_max_bytes": 204800,
            },
            "workspace_root": ".",
            "skills_dir": "./skills",
            "shell_type": "cmd",
            "authorization_level": "auto",
            "plan_mode": False,
            "thinking_mode": False,
        }
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, handle)
        handle.close()
        return handle.name

    def make_app(self, animation=False):
        config_path = self._track_config(self._make_temp_config())
        config = Config(config_path)
        config.ui["workspace_enabled"] = False
        config.ui["dock_width_ratio"] = 0.333
        config.ui["dock_min_width"] = 36
        config.ui["dock_max_width"] = 64
        return KairoApp(config, ToolRegistry(), animation=animation, reduced_motion=not animation)

    def tearDown(self):
        # Clean up temporary config files created by _make_temp_config.
        for path in getattr(self, "_configs", []):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _track_config(self, path: str) -> str:
        if not hasattr(self, "_configs"):
            self._configs = []
        self._configs.append(path)
        return path

    async def test_responsive_dock_and_focus(self):
        narrow = self.make_app()
        async with narrow.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            self.assertTrue(narrow.screen.has_class("narrow"))
            self.assertTrue(narrow.query_one("#composer", Composer).has_focus)

        wide = self.make_app()
        async with wide.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            self.assertFalse(wide.screen.has_class("narrow"))
            self.assertEqual(wide.query_one("#status-dock").region.width, 53)
            await pilot.press("ctrl+b")
            self.assertTrue(wide.query_one(WorkspaceTree).has_focus)
            await pilot.press("ctrl+b")
            self.assertTrue(wide.query_one("#composer", Composer).has_focus)
            wide.query_one("#composer", Composer).text = "draft"
            await pilot.resize_terminal(192, 40)
            await pilot.pause()
            self.assertEqual(wide.query_one("#status-dock").region.width, 64)
            self.assertEqual(wide.query_one("#composer", Composer).text, "draft")

        below_breakpoint = self.make_app()
        async with below_breakpoint.run_test(size=(119, 30)) as pilot:
            await pilot.pause()
            self.assertTrue(below_breakpoint.screen.has_class("narrow"))
            self.assertEqual(below_breakpoint.query_one("#status-dock").region.width, 119)

        at_breakpoint = self.make_app()
        async with at_breakpoint.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            self.assertFalse(at_breakpoint.screen.has_class("narrow"))
            self.assertEqual(at_breakpoint.query_one("#status-dock").region.width, 40)
            diff_region = at_breakpoint.query_one("#diff-viewer").region
            footer_region = at_breakpoint.query_one("#dock-status-footer").region
            dock_region = at_breakpoint.query_one("#status-dock").region
            self.assertLessEqual(diff_region.bottom, footer_region.y)
            self.assertLessEqual(footer_region.bottom, dock_region.bottom)

        capped = self.make_app()
        async with capped.run_test(size=(240, 40)) as pilot:
            await pilot.pause()
            self.assertEqual(capped.query_one("#status-dock").region.width, 64)

    async def test_command_completion_and_new_session(self):
        app = self.make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "/se"
            await pilot.pause()
            await pilot.press("tab")
            self.assertEqual(composer.text, "/sessions ")

            await app.handle_command("/new UI Test")
            self.assertEqual(app.agent.active_session_name, "UI Test")

            composer.text = "/help"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+up")
            self.assertEqual(composer.text, "/help")

    async def test_streaming_worker_updates_history_without_losing_focus(self):
        app = self.make_app()

        def fake_stream(*_args, **_kwargs):
            yield "content", "Hello "
            yield "content", "from Kai"
            yield "usage", {"prompt_tokens": 10, "completion_tokens": 4}

        async with app.run_test(size=(100, 30)) as pilot:
            with patch.object(app.agent.llm, "stream_response", side_effect=fake_stream):
                composer = app.query_one("#composer", Composer)
                composer.text = "hello"
                await pilot.press("enter")
                for _ in range(12):
                    await pilot.pause(0.05)

            self.assertFalse(app.busy)
            self.assertEqual(app.agent.history[-1]["content"], "Hello from Kai")
            self.assertTrue(composer.has_focus)
            self.assertGreaterEqual(len(app.query_one("#conversation", ConversationView).children), 3)

    async def test_reduced_motion_keeps_mascot_static(self):
        app = self.make_app(animation=False)
        async with app.run_test(size=(140, 35)) as pilot:
            mascot = app.query_one("#header-kai", KaiMascot)
            initial = mascot.render()
            mascot.set_state("thinking")
            await pilot.pause(0.3)
            self.assertEqual(mascot.frame_index, 0)
            self.assertNotEqual(str(initial), str(mascot.render()))

    async def test_tool_approval_modal_unblocks_agent_worker(self):
        class DemoTool(BaseTool):
            name = "demo_tool"
            description = "Demo"
            parameters = {"type": "object", "properties": {}}

            def execute(self):
                self.emit_output("live output")
                return "tool result"

        registry = ToolRegistry()
        registry.register(DemoTool())
        config_path = self._track_config(self._make_temp_config())
        config = Config(config_path)
        config.auto_mode = False
        config.ui["workspace_enabled"] = False
        app = KairoApp(config, registry, animation=False, reduced_motion=True)
        responses = [
            [("tool_calls", [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "demo_tool", "arguments": "{}"},
            }])],
            [("content", "finished")],
        ]

        def fake_stream(*_args, **_kwargs):
            yield from responses.pop(0)

        async with app.run_test(size=(120, 35)) as pilot:
            with patch.object(app.agent.llm, "stream_response", side_effect=fake_stream):
                composer = app.query_one("#composer", Composer)
                composer.text = "use the tool"
                await pilot.press("enter")
                for _ in range(10):
                    await pilot.pause(0.03)
                    if len(app.screen_stack) > 1:
                        break
                self.assertGreater(len(app.screen_stack), 1)
                await pilot.press("enter")
                for _ in range(20):
                    await pilot.pause(0.03)
                    if not app.busy:
                        break

            self.assertFalse(app.busy)
            self.assertEqual(app.agent.history[-1]["content"], "finished")
            self.assertIn("tool result", str(app.agent.history))

    async def test_thought_and_long_tool_output_are_expandable(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            view = app.query_one("#conversation", ConversationView)
            await view.start_assistant()
            view.append_thought("reasoning " * 100)
            view.finish_assistant()
            thought = view.query_one(ThoughtView)
            self.assertTrue(thought.has_class("collapsed-thought"))
            thought.on_click()
            self.assertFalse(thought.has_class("collapsed-thought"))

            await view.add_tool_result("demo", "x" * 2000)
            output = view.query_one(ExpandableToolOutput)
            self.assertFalse(output.expanded)
            output.on_click()
            self.assertTrue(output.expanded)
            await pilot.pause()

    async def test_slash_palette_supports_arrows_completion_and_escape(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            composer = app.query_one("#composer", Composer)
            palette = app.query_one("#suggestions", CommandPalette)
            composer.text = "/"
            await pilot.pause()
            self.assertTrue(palette.has_class("visible"))
            self.assertEqual(palette.index, 0)
            self.assertEqual(len(palette.matches), len(COMMAND_CATALOG))

            for _ in range(8):
                await pilot.press("down")
            await pilot.pause()
            self.assertEqual(palette.index, 8)
            self.assertGreater(palette.scroll_y, 0)

            composer.text = "/"
            await pilot.pause()
            await pilot.press("up")
            await pilot.pause()
            self.assertEqual(palette.index, len(COMMAND_CATALOG) - 1)
            self.assertGreater(palette.scroll_y, 0)

            composer.text = "/c"
            await pilot.pause()
            self.assertEqual(palette.matches, ["/clear", "/compress"])
            # Navigate down once to land on "/compress" (index 1) and accept it.
            await pilot.press("down", "enter")
            self.assertEqual(composer.text, "/compress ")

            composer.text = "/se"
            await pilot.pause()
            await pilot.press("enter")
            self.assertEqual(composer.text, "/sessions ")
            self.assertFalse(palette.has_class("visible"))

            composer.text = "/does-not-exist"
            await pilot.pause()
            self.assertFalse(palette.has_class("visible"))

            composer.text = "/"
            await pilot.pause()
            await pilot.press("escape")
            self.assertEqual(composer.text, "/")
            self.assertFalse(palette.has_class("visible"))

    async def test_exact_slash_command_executes(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "/help"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(composer.text, "")
            self.assertGreaterEqual(len(app.query_one("#conversation", ConversationView).children), 2)

    async def test_status_command_can_emit_console_content_on_ui_thread(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "/status"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            self.assertEqual(composer.text, "")
            self.assertGreaterEqual(len(app.query_one("#conversation", ConversationView).children), 2)
            self.assertTrue(composer.has_focus)

    async def test_workspace_modal_and_context_threshold_colors(self):
        app = self.make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.2)
            self.assertTrue(app.screen.has_class("narrow"))
            await pilot.press("ctrl+b")
            await pilot.pause()
            self.assertGreater(len(app.screen_stack), 1)
            self.assertIsInstance(app.screen, WorkspaceModal)
            self.assertIsNotNone(app.screen.query_one("#workspace-modal-title"))
            self.assertIsNotNone(app.screen.query_one("#workspace-actions"))
            self.assertIsNotNone(app.screen.query_one(WorkspacePanel))
            await pilot.press("escape")

            tracker = app.agent.token_tracker
            tracker.context_window = 100
            tracker.set_context_used(70)
            app.refresh_dock()
            self.assertTrue(app.query_one("#context-bar").has_class("context-warning"))
            tracker.set_context_used(90)
            app.refresh_dock()
            self.assertTrue(app.query_one("#context-bar").has_class("context-danger"))

    async def test_workspace_worker_populates_without_losing_composer_focus(self):
        app = self.make_app()
        app.config.ui["workspace_enabled"] = True
        app.config.ui["workspace_refresh_seconds"] = 10.0
        app.config.ui["workspace_max_files"] = 200
        app.workspace_monitor = app._make_workspace_monitor()
        app.workspace_snapshot = WorkspaceSnapshot(root=str(app.workspace_monitor.root))
        async with app.run_test(size=(140, 35)) as pilot:
            for _ in range(30):
                await pilot.pause(0.05)
                if app.workspace_snapshot.files:
                    break
            self.assertTrue(app.workspace_snapshot.files)
            self.assertTrue(app.query_one("#composer", Composer).has_focus)

    async def test_mode_command_sets_authorization_level(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "/mode"
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ModeModal)
            # Default auth is "auto" at index 1; navigate to "manual" (index 0) and save.
            await pilot.press("up", "enter")
            await pilot.pause()
            self.assertEqual(app.config.authorization_level, "manual")

    async def test_mode_command_can_set_yolo_authorization_level(self):
        app = self.make_app()
        async with app.run_test(size=(120, 35)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.text = "/mode"
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ModeModal)
            # Default auth is "auto" at index 1; navigate to "yolo" (index 2) and save.
            await pilot.press("down", "enter")
            await pilot.pause()
            self.assertEqual(app.config.authorization_level, "yolo")

    async def test_workspace_move_command_switches_root(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp) / "other"
            other.mkdir()
            config_path = self._track_config(self._make_temp_config())
            config = Config(config_path)
            config.ui["workspace_enabled"] = False
            config.ui["dock_width_ratio"] = 0.333
            config.ui["dock_min_width"] = 36
            config.ui["dock_max_width"] = 64
            app = KairoApp(config, ToolRegistry(), animation=False, reduced_motion=True)
            async with app.run_test(size=(140, 35)) as pilot:
                composer = app.query_one("#composer", Composer)
                composer.text = f"/workspace {other}"
                await pilot.press("enter")
                for _ in range(20):
                    await pilot.pause(0.05)
                    if app.workspace_context.root.resolve() == other.resolve():
                        break
                self.assertEqual(app.workspace_context.root.resolve(), other.resolve())
                self.assertEqual(app.config.workspace_root, str(other.resolve()))

    async def test_workspace_tree_refreshes_when_file_structures_are_identical(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "old"
            new = Path(temp) / "new"
            old.mkdir()
            new.mkdir()
            (old / "same.txt").write_text("old", encoding="utf-8")
            (new / "same.txt").write_text("new", encoding="utf-8")

            config_path = self._track_config(self._make_temp_config())
            config = Config(config_path)
            config.workspace_root = str(old)
            config.ui["workspace_enabled"] = True
            config.ui["workspace_refresh_seconds"] = 10.0
            app = KairoApp(config, ToolRegistry(), animation=False, reduced_motion=True)
            async with app.run_test(size=(140, 35)) as pilot:
                # Wait for initial scan.
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_snapshot.files:
                        break
                tree = app.query_one("#workspace-tree", WorkspaceTree)
                self.assertEqual(str(tree.root.label), "old")

                composer = app.query_one("#composer", Composer)
                composer.text = f"/workspace {new}"
                await pilot.press("enter")
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_context.root.resolve() == new.resolve():
                        break
                self.assertEqual(app.workspace_context.root.resolve(), new.resolve())
                # Tree root label must reflect the new directory even though file list is identical.
                self.assertEqual(str(tree.root.label), "new")

    async def test_workspace_tree_updates_for_different_file_structures(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "old"
            new = Path(temp) / "new"
            old.mkdir()
            new.mkdir()
            (old / "old_file.txt").write_text("x", encoding="utf-8")
            (new / "new_file.txt").write_text("y", encoding="utf-8")

            config_path = self._track_config(self._make_temp_config())
            config = Config(config_path)
            config.workspace_root = str(old)
            config.ui["workspace_enabled"] = True
            config.ui["workspace_refresh_seconds"] = 10.0
            app = KairoApp(config, ToolRegistry(), animation=False, reduced_motion=True)
            async with app.run_test(size=(140, 35)) as pilot:
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_snapshot.files:
                        break
                tree = app.query_one("#workspace-tree", WorkspaceTree)
                initial_files = set(str(node.label) for node in tree.root.children)
                self.assertIn("old_file.txt", initial_files)

                composer = app.query_one("#composer", Composer)
                composer.text = f"/workspace {new}"
                await pilot.press("enter")
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_context.root.resolve() == new.resolve():
                        break
                self.assertEqual(app.workspace_context.root.resolve(), new.resolve())
                new_files = set(str(node.label) for node in tree.root.children)
                self.assertIn("new_file.txt", new_files)
                self.assertNotIn("old_file.txt", new_files)

    async def test_workspace_switch_consistency_across_components(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "old"
            new = Path(temp) / "new"
            old.mkdir()
            new.mkdir()
            (old / "file.txt").write_text("x", encoding="utf-8")
            (new / "file.txt").write_text("y", encoding="utf-8")

            config_path = self._track_config(self._make_temp_config())
            config = Config(config_path)
            config.workspace_root = str(old)
            config.ui["workspace_enabled"] = True
            config.ui["workspace_refresh_seconds"] = 10.0
            app = KairoApp(config, ToolRegistry(), animation=False, reduced_motion=True)
            async with app.run_test(size=(140, 35)) as pilot:
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_snapshot.files:
                        break

                composer = app.query_one("#composer", Composer)
                composer.text = f"/workspace {new}"
                await pilot.press("enter")
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_context.root.resolve() == new.resolve():
                        break

                tree = app.query_one("#workspace-tree", WorkspaceTree)
                brand = app.query_one("#brand-header", BrandHeader)
                self.assertEqual(app.workspace_context.root.resolve(), new.resolve())
                self.assertEqual(app.workspace_monitor.root.resolve(), new.resolve())
                self.assertEqual(Path(app.workspace_snapshot.root).resolve(), new.resolve())
                self.assertEqual(str(tree.root.label), "new")
                self.assertIn(str(new.resolve()), brand.cwd)
                self.assertEqual(Path(config.workspace_root).resolve(), new.resolve())

    async def test_rapid_workspace_switches_do_not_revert(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp:
            a = Path(temp) / "a"
            b = Path(temp) / "b"
            c = Path(temp) / "c"
            for d in (a, b, c):
                d.mkdir()
                (d / "marker.txt").write_text(d.name, encoding="utf-8")

            config_path = self._track_config(self._make_temp_config())
            config = Config(config_path)
            config.workspace_root = str(a)
            config.ui["workspace_enabled"] = True
            config.ui["workspace_refresh_seconds"] = 10.0
            app = KairoApp(config, ToolRegistry(), animation=False, reduced_motion=True)
            async with app.run_test(size=(140, 35)) as pilot:
                for _ in range(30):
                    await pilot.pause(0.05)
                    if app.workspace_snapshot.files:
                        break

                composer = app.query_one("#composer", Composer)
                for target in (b, c):
                    composer.text = f"/workspace {target}"
                    await pilot.press("enter")

                for _ in range(40):
                    await pilot.pause(0.05)
                    if app.workspace_context.root.resolve() == c.resolve():
                        break

                tree = app.query_one("#workspace-tree", WorkspaceTree)
                self.assertEqual(app.workspace_context.root.resolve(), c.resolve())
                self.assertEqual(str(tree.root.label), "c")
                self.assertEqual(Path(app.workspace_snapshot.root).resolve(), c.resolve())


if __name__ == "__main__":
    unittest.main()
