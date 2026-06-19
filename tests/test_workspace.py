import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.workspace import WorkspaceMonitor


class TestWorkspaceMonitor(unittest.TestCase):
    def test_non_git_scan_ignores_generated_dirs_and_tracks_tool_diff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("old\n", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "hidden.py").write_text("hidden", encoding="utf-8")
            monitor = WorkspaceMonitor(root)

            self.assertIn("src/app.py", monitor.refresh().files)
            self.assertNotIn(".venv/hidden.py", monitor.refresh().files)
            monitor.begin_tool("write_file", {"path": "src/app.py"})
            (root / "src" / "app.py").write_text("new\n", encoding="utf-8")
            monitor.finish_tool("write_file", {"path": "src/app.py"}, True)
            snapshot = monitor.refresh("src/app.py")

            self.assertEqual(snapshot.changes[0].path, "src/app.py")
            self.assertIn("-old", snapshot.diff)
            self.assertIn("+new", snapshot.diff)

    def test_file_limit_marks_tree_as_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(4):
                (root / f"{index}.txt").write_text(str(index), encoding="utf-8")
            snapshot = WorkspaceMonitor(root, max_files=2).refresh()
            self.assertEqual(len(snapshot.files), 2)
            self.assertTrue(snapshot.tree_truncated)

    def test_git_changes_prioritize_session_files_and_render_untracked_diff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            (root / "before.txt").write_text("preexisting\n", encoding="utf-8")
            monitor = WorkspaceMonitor(root)
            (root / "session.txt").write_text("session\n", encoding="utf-8")

            snapshot = monitor.refresh("session.txt")

            self.assertEqual(snapshot.changes[0].path, "session.txt")
            self.assertTrue(snapshot.changes[0].session_touched)
            self.assertIn("+session", snapshot.diff)

    def test_failed_tool_does_not_mark_target(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = WorkspaceMonitor(Path(temp))
            monitor.begin_tool("patch_file", '{"path": "missing.py"}')
            monitor.finish_tool("patch_file", '{"path": "missing.py"}', False)
            self.assertNotIn("missing.py", monitor.session_touched)

    def test_git_staged_deleted_binary_and_truncated_diffs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("base\n", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\0base")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            monitor = WorkspaceMonitor(root, max_diff_bytes=1024)

            (root / "tracked.txt").write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            staged = monitor.refresh("tracked.txt")
            self.assertTrue(next(item for item in staged.changes if item.path == "tracked.txt").staged)
            self.assertIn("+staged", staged.diff)

            (root / "tracked.txt").unlink()
            deleted = monitor.refresh("tracked.txt")
            self.assertIn("D", next(item for item in deleted.changes if item.path == "tracked.txt").status)

            (root / "binary.bin").write_bytes(b"\0changed")
            binary = monitor.refresh("binary.bin")
            self.assertIn("Binary file", binary.diff)

            (root / "large.txt").write_text("x" * 5000, encoding="utf-8")
            large = monitor.refresh("large.txt")
            self.assertTrue(large.diff_truncated)

    def test_git_subdirectory_is_the_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "child"
            child.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "outside.txt").write_text("outside", encoding="utf-8")
            (child / "inside.txt").write_text("inside", encoding="utf-8")

            snapshot = WorkspaceMonitor(child).refresh()

            self.assertIn("inside.txt", snapshot.files)
            self.assertEqual([change.path for change in snapshot.changes], ["inside.txt"])


if __name__ == "__main__":
    unittest.main()
