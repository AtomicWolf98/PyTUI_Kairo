"""Tests for the runtime configuration editor (ConfigDraft)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from agent.config import Config
from agent.config_editor import ConfigDraft, ValidationReport


def _write_config(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _base_data() -> Dict[str, Any]:
    return {
        "llm": {
            "active_provider": "alpha",
            "active_model": "alpha-1",
            "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
            "providers": [
                {
                    "name": "alpha",
                    "base_url": "https://alpha.example.com/v1",
                    "api_key_env": "KAIRO_ALPHA_API_KEY",
                    "models": [
                        {
                            "name": "alpha-1",
                            "temperature": 0.2,
                            "max_tokens": 4000,
                            "context_window": 32000,
                        }
                    ],
                }
            ],
        },
        "sessions": {"enabled": True, "storage_dir": ".kairo/sessions"},
        "workspace_root": ".",
    }


class _TempConfig:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.json"
        _write_config(self.path, _base_data())

    def cleanup(self):
        self.dir.cleanup()


class TestConfigDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = _TempConfig()
        self.config = Config(config_path=str(self.tmp.path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_added_provider_appears_in_draft_and_validates(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.add_provider(
            name="beta",
            base_url="https://beta.example.com/v1",
            api_key_env="KAIRO_BETA_KEY",
            models=[{"name": "beta-1", "context_window": 16000, "max_tokens": 2000, "temperature": 0.3}],
        ))
        names = [p["name"] for p in draft.llm["providers"]]
        self.assertIn("beta", names)
        self.assertTrue(draft.validate().ok)

    def test_add_provider_rejects_duplicate_name(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertFalse(draft.add_provider(
            name="alpha",
            base_url="https://alpha.example.com/v1",
            models=[{"name": "m"}],
        ))

    def test_add_provider_rejects_when_no_models(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertFalse(draft.add_provider(
            name="empty",
            base_url="https://empty.example.com/v1",
            models=[],
        ))

    def test_add_model_to_existing_provider(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertTrue(draft.add_model("alpha", name="alpha-2"))
        provider = next(p for p in draft.llm["providers"] if p["name"] == "alpha")
        self.assertEqual(len(provider["models"]), 2)
        merged = provider["models"][1]
        # Defaults should be filled in.
        self.assertEqual(merged["context_window"], 32000)
        self.assertEqual(merged["max_tokens"], 4000)

    def test_add_model_rejects_duplicate(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertFalse(draft.add_model("alpha", name="alpha-1"))

    def test_remove_model_protects_last_model(self):
        draft = ConfigDraft.from_config(self.config)
        self.assertFalse(draft.remove_model("alpha", "alpha-1"))

    def test_remove_provider_and_active_rescue(self):
        draft = ConfigDraft.from_config(self.config)
        draft.add_provider(
            name="gamma",
            base_url="https://gamma.example.com/v1",
            models=[{"name": "gamma-1"}],
        )
        draft.set_active_model("gamma", "gamma-1")
        self.assertTrue(draft.remove_provider("gamma"))
        self.assertEqual(draft.llm["active_provider"], "alpha")

    def test_validate_flags_bad_url(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", base_url="not-a-url")
        report = draft.validate()
        self.assertFalse(report.ok)
        self.assertTrue(any("base_url" in err for err in report.errors))

    def test_validate_flags_max_tokens_above_context_window(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_model("alpha", "alpha-1", max_tokens=10_000_000, context_window=1000)
        report = draft.validate()
        self.assertFalse(report.ok)
        self.assertTrue(any("max_tokens" in err for err in report.errors))

    def test_validate_flags_temperature_range(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_model("alpha", "alpha-1", temperature=5.0)
        report = draft.validate()
        self.assertFalse(report.ok)

    def test_validate_warns_when_inline_and_env_both_set(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key="sk-inline")
        report = draft.validate()
        self.assertTrue(any("api_key_env" in warn or "inline" in warn for warn in report.warnings))

    def test_apply_to_persists_and_updates_runtime_fields(self):
        os.environ["KAIRO_ALPHA_API_KEY"] = "env-secret"
        try:
            draft = ConfigDraft.from_config(self.config)
            draft.add_provider(
                name="beta",
                base_url="https://beta.example.com/v1",
                api_key_env="KAIRO_BETA_KEY",
                models=[{"name": "beta-1"}],
            )
            draft.set_active_model("beta", "beta-1")
            report = draft.apply_to(self.config, backup=True, allow_inline_key=False)
            self.assertTrue(report.ok, report.to_text())

            with open(self.tmp.path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            provider_names = [p["name"] for p in saved["llm"]["providers"]]
            self.assertIn("beta", provider_names)
            self.assertEqual(saved["llm"]["active_provider"], "beta")
            # env keys never persisted raw value; inline keys stripped.
            raw = json.dumps(saved)
            # Raw env value must never be persisted; the env *name* string is expected to remain.
            self.assertNotIn("env-secret", raw)
        finally:
            os.environ.pop("KAIRO_ALPHA_API_KEY", None)

    def test_apply_to_creates_backup_and_can_restore(self):
        draft1 = ConfigDraft.from_config(self.config)
        draft1.add_provider(
            name="beta",
            base_url="https://beta.example.com/v1",
            api_key_env="KAIRO_BETA_KEY",
            models=[{"name": "beta-1"}],
        )
        report = draft1.apply_to(self.config, backup=True)
        self.assertTrue(report.ok)

        backups = Config.list_backups(self.tmp.path)
        self.assertGreaterEqual(len(backups), 1)

        # Restore from backup: the original single-provider file should reappear.
        restored = Config.restore_backup(self.tmp.path, backups[0]["name"])
        self.assertTrue(restored)
        with open(self.tmp.path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual([p["name"] for p in saved["llm"]["providers"]], ["alpha"])

    def test_apply_to_refuses_validation_errors_without_writing(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", base_url="bad-url")
        original_bytes = self.tmp.path.read_text(encoding="utf-8")
        report = draft.apply_to(self.config, backup=True)
        self.assertFalse(report.ok)
        # File untouched.
        self.assertEqual(self.tmp.path.read_text(encoding="utf-8"), original_bytes)

    def test_apply_to_strips_inline_key_by_default(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key="sk-secret-keep", api_key_env="")
        report = draft.apply_to(self.config, backup=True, allow_inline_key=False)
        self.assertTrue(report.ok)
        with open(self.tmp.path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        provider = next(p for p in saved["llm"]["providers"] if p["name"] == "alpha")
        self.assertNotIn("api_key", provider)
        self.assertNotIn("sk-secret-keep", json.dumps(saved))

    def test_apply_to_keeps_inline_key_when_explicitly_allowed(self):
        draft = ConfigDraft.from_config(self.config)
        draft.update_provider("alpha", api_key="sk-secret-keep", api_key_env="")
        report = draft.apply_to(self.config, backup=True, allow_inline_key=True)
        self.assertTrue(report.ok)
        with open(self.tmp.path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        provider = next(p for p in saved["llm"]["providers"] if p["name"] == "alpha")
        self.assertEqual(provider.get("api_key"), "sk-secret-keep")

    def test_apply_to_rolls_back_on_save_failure(self):
        original_llm = json.loads(json.dumps(self.config.llm))

        def fake_save(*args, **kwargs):
            raise OSError("simulated write failure")

        self.config.save = fake_save  # type: ignore[method-assign]
        draft = ConfigDraft.from_config(self.config)
        draft.add_provider(
            name="beta",
            base_url="https://beta.example.com/v1",
            api_key_env="KAIRO_BETA_KEY",
            models=[{"name": "beta-1"}],
        )
        report = draft.apply_to(self.config, backup=False)
        self.assertFalse(report.ok, report.to_text())
        # In-memory config rolled back.
        self.assertEqual([p["name"] for p in self.config.llm["providers"]], [p["name"] for p in original_llm["providers"]])
        # On-disk file untouched.
        with open(self.tmp.path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual([p["name"] for p in saved["llm"]["providers"]], ["alpha"])


if __name__ == "__main__":
    unittest.main()