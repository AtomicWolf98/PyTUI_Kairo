import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.context_manager import RUNTIME_STATE_NAME
from agent.runtime import KairoRuntime


class TestRuntimeLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "llm": {
                        "active_profile": "test/test-model",
                        "defaults": {
                            "temperature": 0.2,
                            "max_tokens": 100,
                            "context_window": 10000,
                        },
                        "profiles": [
                            {
                                "id": "test/test-model",
                                "label": "Test",
                                "provider": "test",
                                "base_url": "https://example.test/v1",
                                "model": "test-model",
                                "temperature": 0.2,
                                "max_tokens": 100,
                                "context_window": 10000,
                            }
                        ],
                    },
                    "workspace_root": str(self.workspace),
                    "skills_dir": "./skills",
                    "sessions": {"enabled": False},
                    "web": {"max_event_buffer": 1000},
                }
            ),
            encoding="utf-8",
        )
        self.config = Config(str(self.config_path))
        self.runtime = KairoRuntime(self.config)

    def tearDown(self):
        self.runtime.shutdown()
        self.tmp.cleanup()

    def _wait_idle(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.runtime.is_busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.runtime.is_busy(), "runtime worker did not become idle")

    def test_busy_is_set_before_thread_start_and_blocks_mutations(self):
        entered = threading.Event()
        release = threading.Event()

        def run(_text, emit, **_kwargs):
            entered.set()
            release.wait(2)
            emit("message_started", {"kind": "assistant"})
            self.runtime.agent.history.append({"role": "assistant", "content": "done"})
            emit("message_finished", None)

        with patch.object(self.runtime.agent.runner, "run_interaction_events", side_effect=run):
            submitted = self.runtime.submit_message("hold")
            self.assertTrue(submitted["ok"])
            self.assertTrue(self.runtime.is_busy())
            self.assertTrue(entered.wait(1))

            before = self.config.workspace_bookmarks
            results = [
                self.runtime.sessions.create("blocked"),
                self.runtime.workspace.add_bookmark("blocked", str(self.workspace)),
                self.runtime.workspace.move(str(self.root / "does-not-exist")),
                self.runtime.config_service.update_settings("general", {"plan_mode": True}),
                self.runtime.skills.reload(),
            ]
            self.assertTrue(all(item.get("code") == "runtime_busy" for item in results))
            self.assertEqual(self.config.workspace_bookmarks, before)
            self.assertFalse(self.config.plan_mode)

            release.set()
            self._wait_idle()

    def test_turn_stays_bound_to_captured_session_during_bypass_switch(self):
        manager = self.runtime.agent.conversations
        first = manager.active
        second = manager.create_session("Second")
        manager.switch_session(first.id)
        entered = threading.Event()
        release = threading.Event()

        def run(_text, emit, **_kwargs):
            del emit
            entered.set()
            release.wait(2)
            self.runtime.agent.history.append({"role": "assistant", "content": "belongs-to-first"})
            self.runtime.agent.conversations.mark_dirty(reason="test")

        with patch.object(self.runtime.agent.runner, "run_interaction_events", side_effect=run):
            submitted = self.runtime.submit_message("pin this turn")
            self.assertEqual(submitted["session_id"], first.id)
            self.assertTrue(entered.wait(1))

            # Deliberately bypass SessionService to simulate stale/internal code.
            manager.active_session_id = second.id
            release.set()
            self._wait_idle()

        self.assertTrue(any(m.get("content") == "belongs-to-first" for m in first.history))
        self.assertFalse(any(m.get("content") == "belongs-to-first" for m in second.history))

    def test_workspace_commit_updates_all_runtime_roots_and_revision(self):
        target = self.root / "target"
        target.mkdir()
        result = self.runtime.workspace.move(str(target))

        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(self.config.workspace_root).resolve(), target.resolve())
        self.assertEqual(self.runtime.agent.workspace_context.root, target.resolve())
        self.assertEqual(self.runtime.workspace.monitor.root, target.resolve())
        self.assertEqual(self.runtime.workspace_revision, 1)
        self.assertEqual(result["runtime_id"], self.runtime.runtime_id)
        self.assertEqual(result["snapshot"]["workspace_revision"], 1)
        shell = self.runtime.agent.registry.tools["run_command"]
        self.assertEqual(Path(shell.session.cwd).resolve(), target.resolve())
        for session in self.runtime.agent.conversations.sessions:
            runtime_message = next(
                message
                for message in session.history
                if message.get("name") == RUNTIME_STATE_NAME
            )
            self.assertIn(str(target.resolve()), runtime_message["content"])

        changed = [event for event in self.runtime.events.snapshot() if event.kind == "workspace_changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].payload["workspace_revision"], 1)

    def test_workspace_write_probe_fails_once_without_hanging(self):
        target = self.root / "permission-denied"
        target.mkdir()
        old_root = self.runtime.agent.workspace_context.root

        with patch("agent.runtime.os.open", side_effect=PermissionError("denied")) as probe:
            result = self.runtime.workspace.move(str(target))

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_workspace")
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(self.runtime.agent.workspace_context.root, old_root)
        self.assertEqual(self.runtime.workspace_revision, 0)

    def test_workspace_failure_rolls_config_and_runtime_back(self):
        old = self.workspace.resolve()
        target = self.root / "target"
        target.mkdir()
        original_reload = self.runtime.agent.registry.reload_custom_skills

        def fail_new(*args, workspace_root=None, **kwargs):
            if Path(workspace_root).resolve() == target.resolve():
                raise RuntimeError("injected skill reconcile failure")
            return original_reload(*args, workspace_root=workspace_root, **kwargs)

        with patch.object(
            self.runtime.agent.registry,
            "reload_custom_skills",
            side_effect=fail_new,
        ):
            result = self.runtime.workspace.move(str(target))

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "runtime_sync_failed")
        self.assertEqual(Path(self.config.workspace_root).resolve(), old)
        self.assertEqual(self.runtime.agent.workspace_context.root, old)
        self.assertEqual(self.runtime.workspace.monitor.root, old)
        self.assertEqual(self.runtime.workspace_revision, 0)
        self.assertFalse(self.runtime.status()["lifecycle"]["degraded"])
        self.assertFalse(any(event.kind == "workspace_changed" for event in self.runtime.events.snapshot()))
        self.assertTrue(any(event.kind == "workspace_change_failed" for event in self.runtime.events.snapshot()))

    def test_failed_rollback_enters_degraded_and_refuses_new_work(self):
        draft = self.runtime.config_service._commit_draft
        with patch.object(
            self.runtime.config_service,
            "_sync_runtime_after_commit",
            side_effect=[
                {"ok": False, "error": "apply failed"},
                {"ok": False, "error": "rollback failed"},
            ],
        ):
            result = self.runtime.config_service.update_settings("general", {"plan_mode": True})

        self.assertFalse(result["ok"])
        self.assertTrue(self.runtime.status()["lifecycle"]["degraded"])
        blocked = self.runtime.submit_message("must fail closed")
        self.assertEqual(blocked["code"], "runtime_degraded")
        self.assertTrue(callable(draft))

    def test_worker_failure_clears_busy_and_finishes_once(self):
        with patch.object(
            self.runtime.agent.runner,
            "run_interaction_events",
            side_effect=RuntimeError("injected worker failure"),
        ):
            submitted = self.runtime.submit_message("fail")
            self.assertTrue(submitted["ok"])
            self._wait_idle()

        finished = [
            event
            for event in self.runtime.events.snapshot()
            if event.kind == "turn_finished" and event.payload.get("turn_id") == submitted["turn_id"]
        ]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0].payload["status"], "failed")

    def test_cleanup_failure_takes_precedence_over_stop(self):
        entered = threading.Event()

        def run(_text, emit, cancel_token=None, **_kwargs):
            del emit
            entered.set()
            deadline = time.monotonic() + 2
            while not cancel_token.cancelled and time.monotonic() < deadline:
                time.sleep(0.01)

        with (
            patch.object(self.runtime.agent.runner, "run_interaction_events", side_effect=run),
            patch.object(self.runtime.workspace, "refresh", side_effect=RuntimeError("cleanup failed")),
        ):
            submitted = self.runtime.submit_message("stop")
            self.assertTrue(entered.wait(1))
            self.runtime.stop_current_task()
            self._wait_idle()

        finished = [
            event
            for event in self.runtime.events.snapshot()
            if event.kind == "turn_finished" and event.payload.get("turn_id") == submitted["turn_id"]
        ]
        self.assertEqual(finished[0].payload["status"], "failed")

    def test_session_service_distinguishes_invalid_unknown_and_last(self):
        invalid = self.runtime.sessions.switch("../config")
        self.assertEqual(invalid["code"], "invalid_session_id")

        unknown = "a" * 32
        self.assertEqual(self.runtime.sessions.switch(unknown)["code"], "session_not_found")
        self.assertEqual(self.runtime.sessions.delete(unknown)["code"], "session_not_found")
        self.assertEqual(self.runtime.sessions.export(unknown)["code"], "session_not_found")

        only = self.runtime.agent.conversations.active.id
        self.assertEqual(self.runtime.sessions.delete(only)["code"], "last_session")


if __name__ == "__main__":
    unittest.main()
