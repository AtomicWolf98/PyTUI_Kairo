import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.release_check import ROOT, check_source_tree, check_wheel, source_version, tui_version


class TestReleasePackaging(unittest.TestCase):
    def test_source_versions_are_0_4_0a2(self):
        self.assertEqual(check_source_tree(), "0.4.0a2")
        self.assertEqual(source_version(), "0.4.0a2")
        self.assertEqual(tui_version(), "0.4.0a2")

    def test_wheel_validator_rejects_legacy_agent_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "kairo_tui-0.4.0a2-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "kairo_tui-0.4.0a2.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: kairo-tui\nVersion: 0.4.0a2\n",
                )
                archive.writestr("agent/ui/app.py", "raise NotImplementedError\n")
            with self.assertRaisesRegex(RuntimeError, "unexpected entries"):
                check_wheel(wheel, "0.4.0a2", "kairo_tui")

    def test_wheel_validator_rejects_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "kairo_tui-0.4.0a2-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "kairo_tui-0.4.0a2.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: kairo-tui\nVersion: 0.4.0a1\n",
                )
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                check_wheel(wheel, "0.4.0a2", "kairo_tui")

    def test_installer_is_scoped_to_owned_installation(self):
        installer = (ROOT / "install.bat").read_text(encoding="utf-8")
        self.assertIn("install-owner.ini", installer)
        self.assertIn("KAIRO_INSTALL_ROOT", installer)
        self.assertNotIn("pip uninstall", installer.lower())
        self.assertNotIn("del /f", installer.lower())
        self.assertNotIn("pyTUI", installer)


if __name__ == "__main__":
    unittest.main()
