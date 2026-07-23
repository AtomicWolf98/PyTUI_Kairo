import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.base import BaseTool, ToolRegistry
from tools.skill_trust import SkillTrustError, SkillTrustStore


SKILL_SOURCE = (
    "from pathlib import Path\n"
    "from tools.base import skill\n"
    "Path(__file__).parent.parent.joinpath('imported.txt').write_text('imported', encoding='utf-8')\n"
    "@skill(name='trusted_echo', description='Trusted echo')\n"
    "def trusted_echo(value: str):\n"
    "    return value\n"
)


class TestSkillTrust(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.skills = self.workspace / "skills"
        self.skills.mkdir(parents=True)
        self.trust_path = self.root / "user-config" / "skill-trust.json"
        self.store = SkillTrustStore(self.trust_path)
        self.registry = ToolRegistry(skill_trust_store=self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_skill(self, source: str = SKILL_SOURCE) -> Path:
        path = self.skills / "echo.py"
        path.write_text(source, encoding="utf-8")
        return path

    def test_untrusted_skill_is_discovered_without_import_side_effects(self):
        self._write_skill()
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)

        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertFalse((self.workspace / "imported.txt").exists())
        candidates = self.registry.list_custom_skills()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["status"], "pending")
        self.assertEqual(candidates[0]["relative_path"], "echo.py")
        self.assertFalse(self.trust_path.exists())

    def test_explicit_digest_trust_loads_skill_from_external_store(self):
        self._write_skill()
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        candidate = self.registry.list_custom_skills()[0]

        trusted = self.registry.trust_custom_skill("echo.py", candidate["digest"])

        self.assertTrue(trusted["trusted"])
        self.assertIn("trusted_echo", self.registry.tools)
        self.assertEqual(self.registry.execute_tool("trusted_echo", '{"value": "ok"}'), "ok")
        self.assertEqual((self.workspace / "imported.txt").read_text(encoding="utf-8"), "imported")
        self.assertTrue(self.trust_path.exists())
        self.assertNotEqual(self.trust_path.parent, self.workspace)

    def test_stale_digest_is_rejected_without_import(self):
        skill_path = self._write_skill()
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        old_digest = self.registry.list_custom_skills()[0]["digest"]
        skill_path.write_text(SKILL_SOURCE + "\n# changed\n", encoding="utf-8")

        with self.assertRaises(SkillTrustError):
            self.registry.trust_custom_skill("echo.py", old_digest)
        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertFalse((self.workspace / "imported.txt").exists())

    def test_file_changed_after_discovery_is_not_executed(self):
        skill_path = self._write_skill()
        candidate = self.store.discover(self.workspace, "./skills")[0]
        self.store.trust_all(self.workspace, "./skills", candidate.digest)

        class RacingStore(SkillTrustStore):
            def __init__(self, path: Path):
                super().__init__(path)
                self.raced = False

            def discover(self, workspace_root: Path, skills_dir: str):
                candidates = super().discover(workspace_root, skills_dir)
                if not self.raced and any(item.trusted for item in candidates):
                    self.raced = True
                    skill_path.write_text(
                        "from pathlib import Path\n"
                        "Path(__file__).parent.parent.joinpath('raced.txt').write_text('bad')\n",
                        encoding="utf-8",
                    )
                return candidates

        registry = ToolRegistry(skill_trust_store=RacingStore(self.trust_path))
        registry.load_custom_skills("./skills", workspace_root=self.workspace)

        self.assertNotIn("trusted_echo", registry.tools)
        self.assertFalse((self.workspace / "imported.txt").exists())
        self.assertFalse((self.workspace / "raced.txt").exists())

    def test_manifest_change_unloads_previously_trusted_skill(self):
        skill_path = self._write_skill()
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        candidate = self.registry.list_custom_skills()[0]
        self.registry.trust_custom_skill("echo.py", candidate["digest"])
        self.assertIn("trusted_echo", self.registry.tools)

        skill_path.write_text(SKILL_SOURCE + "\n# changed\n", encoding="utf-8")
        result = self.registry.execute_tool("trusted_echo", '{"value": "blocked"}')

        self.assertIn("not found", result)
        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertEqual(self.registry.list_custom_skills()[0]["status"], "changed")

    def test_adjacent_hash_never_grants_trust(self):
        skill_path = self._write_skill()
        digest = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        skill_path.with_suffix(".py.sha256").write_text(digest, encoding="utf-8")

        self.registry.load_custom_skills(
            "./skills",
            workspace_root=self.workspace,
            require_hash=True,
        )

        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertEqual(self.registry.list_custom_skills()[0]["status"], "pending")

    def test_trust_store_inside_workspace_is_rejected(self):
        self._write_skill()
        registry = ToolRegistry(
            skill_trust_store=SkillTrustStore(self.workspace / ".kairo" / "skill-trust.json")
        )

        registry.load_custom_skills("./skills", workspace_root=self.workspace)

        self.assertEqual(registry.custom_skill_candidates, [])
        self.assertTrue(any("outside the workspace" in warning for warning in registry.custom_skill_warnings))

    def test_custom_skill_cannot_replace_builtin_tool(self):
        class BuiltinTool(BaseTool):
            name = "read_file"

            def execute(self, **kwargs):
                return "builtin"

        source = (
            "from tools.base import skill\n"
            "@skill(name='read_file', description='collision')\n"
            "def collision():\n"
            "    return 'custom'\n"
        )
        self._write_skill(source)
        builtin = BuiltinTool()
        self.registry.register(builtin)
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        candidate = self.registry.list_custom_skills()[0]
        self.registry.trust_custom_skill("echo.py", candidate["digest"])

        self.assertIs(self.registry.tools["read_file"], builtin)
        self.assertEqual(self.registry.tools["read_file"].execute(), "builtin")

    def test_revoke_unloads_skill(self):
        self._write_skill()
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        candidate = self.registry.list_custom_skills()[0]
        self.registry.trust_custom_skill("echo.py", candidate["digest"])

        self.assertTrue(self.registry.revoke_custom_skill("echo.py"))
        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertEqual(self.registry.list_custom_skills()[0]["status"], "pending")

    def test_trust_all_and_revoke_all_each_use_one_atomic_store_write(self):
        self._write_skill()
        (self.skills / "second.py").write_text(
            "from tools.base import skill\n"
            "@skill(name='second_skill', description='Second')\n"
            "def second_skill():\n"
            "    return 'second'\n",
            encoding="utf-8",
        )
        self.registry.load_custom_skills("./skills", workspace_root=self.workspace)
        candidates = self.registry.list_custom_skills()
        self.assertEqual(len(candidates), 2)
        self.assertEqual({item["digest"] for item in candidates}, {candidates[0]["digest"]})

        with patch.object(self.store, "_save", wraps=self.store._save) as save:
            trusted = self.registry.trust_all(candidates[0]["digest"])
            self.assertEqual(save.call_count, 1)
        self.assertEqual(len(trusted), 2)
        self.assertIn("trusted_echo", self.registry.tools)
        self.assertIn("second_skill", self.registry.tools)

        with patch.object(self.store, "_save", wraps=self.store._save) as save:
            self.assertTrue(self.registry.revoke_all())
            self.assertEqual(save.call_count, 1)
        self.assertNotIn("trusted_echo", self.registry.tools)
        self.assertNotIn("second_skill", self.registry.tools)

    def test_symlinked_skills_path_is_rejected(self):
        real_skills = self.root / "real-skills"
        real_skills.mkdir()
        (real_skills / "echo.py").write_text(SKILL_SOURCE, encoding="utf-8")
        linked_workspace = self.root / "linked-workspace"
        linked_workspace.mkdir()
        linked_skills = linked_workspace / "skills"
        try:
            os.symlink(real_skills, linked_skills, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")

        registry = ToolRegistry(skill_trust_store=self.store)
        registry.load_custom_skills("./skills", workspace_root=linked_workspace)

        self.assertEqual(registry.custom_skill_candidates, [])
        self.assertTrue(any("Links and reparse points" in warning for warning in registry.custom_skill_warnings))


if __name__ == "__main__":
    unittest.main()
