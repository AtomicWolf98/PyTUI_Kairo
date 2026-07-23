import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.release_check import ROOT, check_source_tree, check_wheel, source_version


class TestReleasePackaging(unittest.TestCase):
    def test_release_metadata_and_built_assets_match(self):
        self.assertEqual(check_source_tree(), "0.3.3")
        self.assertEqual(source_version(), "0.3.3")

    def test_installer_is_scoped_to_owned_installation(self):
        installer = (ROOT / "install.bat").read_text(encoding="utf-8")
        self.assertIn("install-owner.ini", installer)
        self.assertIn("KAIRO_INSTALL_ROOT", installer)
        self.assertNotIn("pip uninstall", installer.lower())
        self.assertNotIn("del /f", installer.lower())
        self.assertNotIn("pyTUI", installer)

    def test_wheel_payload_validator_rejects_missing_referenced_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "kairo_agent-0.3.3-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "kairo_agent-0.3.3.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: kairo-agent\nVersion: 0.3.3\n",
                )
                archive.writestr(
                    "agent/web/static/index.html",
                    '<script type="module" src="/assets/missing.js"></script>',
                )
                archive.writestr("agent/web/static/version.json", json.dumps({"version": "0.3.3"}))
            with self.assertRaisesRegex(RuntimeError, "missing referenced assets"):
                check_wheel(wheel, "0.3.3")


if __name__ == "__main__":
    unittest.main()
