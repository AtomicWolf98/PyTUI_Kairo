import json
import os
import socket
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from agent.config import Config
from tools.file_ops import ReadFileTool, WriteFileTool, ListDirTool
from tools.patch_ops import PatchFileTool, SearchFileTool
from tools.policy import (
    AUTHORIZATION_AUTO,
    AUTHORIZATION_MANUAL,
    AUTHORIZATION_YOLO,
    CommandPolicy,
    NetworkPolicy,
    OperationScope,
    Permission,
    SecurityError,
    WorkspacePathPolicy,
    classify_command_scope,
    classify_python_scope,
    is_authorized,
)
from tools.shell import ShellExecutor
from tools.web import WebFetchTool


class TestWorkspacePathPolicy(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.policy = WorkspacePathPolicy(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_allows_inside_path(self):
        resolved = self.policy.resolve("sub/file.txt")
        self.assertEqual(resolved, self.root / "sub" / "file.txt")

    def test_rejects_parent_escape(self):
        with self.assertRaises(SecurityError):
            self.policy.resolve("../outside.txt")

    def test_rejects_absolute_outside(self):
        with self.assertRaises(SecurityError):
            self.policy.resolve("C:/Windows/system.ini" if os.name == "nt" else "/etc/passwd")


class TestNetworkPolicy(TestCase):
    def test_rejects_private_ip(self):
        policy = NetworkPolicy()
        with self.assertRaises(SecurityError):
            policy.validate_url("http://192.168.1.1/page")

    def test_rejects_loopback(self):
        policy = NetworkPolicy()
        with self.assertRaises(SecurityError):
            policy.validate_url("http://127.0.0.1/page")

    def test_allows_public_host(self):
        policy = NetworkPolicy()
        with patch("tools.policy.socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
        ]):
            policy.validate_url("https://example.com/page")  # should not raise

    def test_allow_hosts_restricts_others(self):
        policy = NetworkPolicy(allow_hosts=["example.com"])
        with self.assertRaises(SecurityError):
            policy.validate_url("https://other.com/page")

    def test_localhost_is_rejected(self):
        policy = NetworkPolicy()
        with self.assertRaises(SecurityError):
            policy.validate_url("http://localhost/page")

    def test_dns_resolves_to_private_ip(self):
        policy = NetworkPolicy()
        with patch("tools.policy.socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 80)),
        ]):
            with self.assertRaises(SecurityError):
                policy.validate_url("http://private.example.com/page")

    def test_ipv6_loopback_is_rejected(self):
        policy = NetworkPolicy()
        with self.assertRaises(SecurityError):
            policy.validate_url("http://[::1]/page")

    def test_decimal_ip_literal_is_rejected(self):
        policy = NetworkPolicy()
        with self.assertRaises(SecurityError):
            policy.validate_url("http://2130706433/page")


class TestCommandPolicy(TestCase):
    def test_blocks_chaining_metacharacters(self):
        policy = CommandPolicy()
        allowed, reason = policy.classify("echo a; echo b")
        self.assertFalse(allowed)
        self.assertIn("chaining", reason)

    def test_allows_simple_command(self):
        policy = CommandPolicy()
        allowed, reason = policy.classify("echo hello")
        self.assertTrue(allowed)
        self.assertIsNone(reason)


class TestToolPermissions(TestCase):
    def test_permissions(self):
        self.assertEqual(ReadFileTool().permission, Permission.READ)
        self.assertEqual(WriteFileTool().permission, Permission.WRITE)
        self.assertEqual(ListDirTool().permission, Permission.READ)
        self.assertEqual(PatchFileTool().permission, Permission.WRITE)
        self.assertEqual(SearchFileTool().permission, Permission.READ)
        self.assertEqual(WebFetchTool().permission, Permission.NETWORK)
        self.assertEqual(ShellExecutor.permission, Permission.EXECUTE)


class TestWriteFileToolSandbox(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rejects_writing_outside_workspace(self):
        tool = WriteFileTool()
        tool.policy = WorkspacePathPolicy(self.root)
        result = tool.execute(path="../escape.txt", content="bad")
        self.assertTrue(result.startswith("Error:"))
        self.assertNotIn("Successfully", result)


class TestConfigApiKeySource(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()
        for key in ("KAIRO_TEST_API_KEY", "OPENAI_API_KEY"):
            if key in os.environ:
                del os.environ[key]

    def test_env_api_key_is_not_saved(self):
        os.environ["KAIRO_TEST_API_KEY"] = "runtime-secret"
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
                        "api_key_env": "KAIRO_TEST_API_KEY",
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
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.api_key, "runtime-secret")

        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        self.assertNotIn("api_key", saved["llm"]["providers"][0])
        self.assertNotIn("runtime-secret", json.dumps(saved))


class TestAuthorizationLevels(TestCase):
    def test_manual_requires_confirmation_for_all_scopes(self):
        self.assertFalse(is_authorized(AUTHORIZATION_MANUAL, OperationScope.INTERNAL))
        self.assertFalse(is_authorized(AUTHORIZATION_MANUAL, OperationScope.EXTERNAL))
        self.assertFalse(is_authorized(AUTHORIZATION_MANUAL, OperationScope.SYSTEM))
        self.assertFalse(is_authorized(AUTHORIZATION_MANUAL, OperationScope.DESTRUCTIVE))

    def test_auto_allows_internal_only(self):
        self.assertTrue(is_authorized(AUTHORIZATION_AUTO, OperationScope.INTERNAL))
        self.assertFalse(is_authorized(AUTHORIZATION_AUTO, OperationScope.EXTERNAL))
        self.assertFalse(is_authorized(AUTHORIZATION_AUTO, OperationScope.SYSTEM))
        self.assertFalse(is_authorized(AUTHORIZATION_AUTO, OperationScope.DESTRUCTIVE))

    def test_yolo_allows_everything(self):
        for scope in OperationScope:
            self.assertTrue(is_authorized(AUTHORIZATION_YOLO, scope))


class TestCommandScopeClassification(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.policy = WorkspacePathPolicy(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_internal_simple_command(self):
        self.assertEqual(classify_command_scope("echo hello", self.policy), OperationScope.INTERNAL)

    def test_system_package_manager(self):
        self.assertEqual(classify_command_scope("apt install python3", self.policy), OperationScope.SYSTEM)
        self.assertEqual(classify_command_scope("brew install node", self.policy), OperationScope.SYSTEM)

    def test_destructive_recursive_delete(self):
        self.assertEqual(classify_command_scope("rm -rf /", self.policy), OperationScope.DESTRUCTIVE)

    def test_external_absolute_path(self):
        self.assertEqual(classify_command_scope("cat /etc/passwd", self.policy), OperationScope.EXTERNAL)

    def test_cd_parent_is_system(self):
        self.assertEqual(classify_command_scope("cd ..", self.policy), OperationScope.SYSTEM)

    def test_quoted_outside_path_is_external(self):
        self.assertEqual(classify_command_scope('type "C:\\Windows\\system.ini"', self.policy), OperationScope.EXTERNAL)

    def test_pipe_is_system(self):
        self.assertEqual(classify_command_scope("echo ok | findstr ok", self.policy), OperationScope.SYSTEM)

    def test_redirect_is_system(self):
        self.assertEqual(classify_command_scope("echo x > file.txt", self.policy), OperationScope.SYSTEM)

    def test_env_variable_is_system(self):
        self.assertEqual(classify_command_scope("echo %PATH%", self.policy), OperationScope.SYSTEM)

    def test_and_chain_is_system(self):
        self.assertEqual(classify_command_scope("echo a && echo b", self.policy), OperationScope.SYSTEM)


class TestPythonScopeClassification(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.policy = WorkspacePathPolicy(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_internal_pure_calculation(self):
        code = "print(1 + 1)"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.INTERNAL)

    def test_system_subprocess(self):
        code = "import subprocess; subprocess.run(['ls'])"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.SYSTEM)

    def test_destructive_rmtree(self):
        code = "import shutil; shutil.rmtree('/tmp/x')"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.DESTRUCTIVE)

    def test_external_file_open(self):
        code = "with open('/etc/passwd') as f: print(f.read())"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.EXTERNAL)

    def test_pathlib_write_text_is_external(self):
        code = "from pathlib import Path; Path('x.txt').write_text('hi')"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.EXTERNAL)

    def test_io_open_is_external(self):
        code = "import io; io.open('x.txt')"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.EXTERNAL)

    def test_eval_is_system(self):
        code = "eval('1+1')"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.SYSTEM)

    def test_importlib_is_system(self):
        code = "import importlib; importlib.import_module('os')"
        self.assertEqual(classify_python_scope(code, self.policy), OperationScope.SYSTEM)


class TestConfigAuthorizationAndWorkspace(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_legacy_auto_mode_maps_to_auto_level(self):
        data = {
            "llm": {
                "active_provider": "test",
                "active_model": "test-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "providers": [],
            },
            "auto_mode": True,
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.authorization_level, AUTHORIZATION_AUTO)

    def test_authorization_level_round_trip(self):
        data = {
            "llm": {
                "active_provider": "test",
                "active_model": "test-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "providers": [],
            },
            "authorization_level": "yolo",
            "workspace_root": "./src",
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.authorization_level, AUTHORIZATION_YOLO)
        self.assertEqual(config.workspace_root, "./src")

        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved.get("authorization_level"), AUTHORIZATION_YOLO)
        self.assertEqual(saved.get("workspace_root"), "./src")
        self.assertNotIn("auto_mode", saved)


class TestWorkspaceMoveCommand(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        data = {
            "llm": {
                "active_provider": "test",
                "active_model": "test-model",
                "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 128000},
                "providers": [],
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.other_dir = Path(self.temp_dir.name) / "other"
        self.other_dir.mkdir()
        self.agent = None

    def tearDown(self):
        if self.agent is not None:
            self.agent.shutdown()
        self.temp_dir.cleanup()

    def test_workspace_move_updates_root(self):
        from agent.bootstrap import build_agent
        from agent.config import Config

        config = Config(config_path=str(self.config_path))
        self.agent = build_agent(config)
        handled = self.agent.handle_command(f"/workspace move {self.other_dir}")
        self.assertTrue(handled)
        self.assertEqual(Path(config.workspace_root).resolve(), self.other_dir.resolve())

    def test_workspace_move_rejects_nonexistent_path(self):
        from agent.bootstrap import build_agent
        from agent.config import Config

        config = Config(config_path=str(self.config_path))
        self.agent = build_agent(config)
        handled = self.agent.handle_command("/workspace move /does/not/exist")
        self.assertTrue(handled)
        self.assertNotEqual(Path(config.workspace_root).resolve(), Path("/does/not/exist").resolve())

    def test_workspace_move_does_not_delete_existing_sentinel(self):
        from agent.bootstrap import build_agent
        from agent.config import Config

        sentinel = self.other_dir / ".kairo_write_test"
        sentinel.write_text("user data", encoding="utf-8")

        config = Config(config_path=str(self.config_path))
        self.agent = build_agent(config)
        handled = self.agent.handle_command(f"/workspace move {self.other_dir}")
        self.assertTrue(handled)
        self.assertTrue(sentinel.exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data")

    def test_workspace_move_updates_tool_policy_roots(self):
        from agent.bootstrap import build_agent
        from agent.config import Config
        from tools.file_ops import ReadFileTool

        config = Config(config_path=str(self.config_path))
        self.agent = build_agent(config)
        read_tool = self.agent.registry.tools["read_file"]
        self.assertIsInstance(read_tool, ReadFileTool)
        initial_root = read_tool.policy.root

        handled = self.agent.handle_command(f"/workspace move {self.other_dir}")
        self.assertTrue(handled)
        self.assertEqual(read_tool.policy.root.resolve(), self.other_dir.resolve())
        self.assertNotEqual(read_tool.policy.root.resolve(), initial_root.resolve())
