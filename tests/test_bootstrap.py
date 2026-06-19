import unittest

from agent.bootstrap import build_agent, build_registry
from agent.config import Config


class TestBootstrap(unittest.TestCase):
    def test_build_registry_registers_expected_builtin_tools(self):
        config = Config()
        registry = build_registry(config)
        self.assertEqual(
            set(registry.tools),
            {
                "run_command",
                "run_python_code",
                "read_file",
                "write_file",
                "list_dir",
                "web_fetch",
                "search_file",
                "patch_file",
            },
        )

    def test_build_agent_reuses_bootstrap_registry(self):
        config = Config()
        agent = build_agent(config)
        self.assertIn("run_command", agent.registry.tools)
        self.assertIn("patch_file", agent.registry.tools)


if __name__ == "__main__":
    unittest.main()
