import unittest
import os
import json
from pathlib import Path
import tempfile
from agent.config import Config

class TestConfig(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for config testing
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "test_config.json"
        
        self.default_data = {
            "api_key": "test_key",
            "base_url": "https://test.api.com",
            "model": "test_model",
            "active_model_profile": "other",
            "model_profiles": [
                {
                    "name": "test",
                    "api_key": "test_key",
                    "base_url": "https://test.api.com",
                    "model": "test_model",
                    "temperature": 0.5,
                    "max_tokens": 1000,
                    "context_window": 64000
                },
                {
                    "name": "other",
                    "api_key": "other_key",
                    "base_url": "https://other.api.com",
                    "model": "other_model",
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "context_window": 32000,
                    "context_management": {
                        "trigger_percent": 75,
                        "target_percent": 50,
                        "preserve_recent_turns": 2
                    }
                }
            ],
            "temperature": 0.5,
            "max_tokens": 1000,
            "context_window": 64000,
            "context_management": {
                "enabled": True,
                "auto_compress": True,
                "trigger_percent": 85,
                "target_percent": 60,
                "preserve_recent_turns": 4
            },
            "ui": {
                "mode": "auto",
                "theme": "kairo-dark",
                "animation": "full",
                "mascot": True,
                "dock_breakpoint": 132,
                "dock_width": 50,
                "reduced_motion": False
            },
            "skills_dir": "./test_skills",
            "auto_mode": True,
            "plan_mode": True,
            "thinking_mode": True
        }
        
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.default_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean environment variables if overridden
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        if "OPENAI_BASE_URL" in os.environ:
            del os.environ["OPENAI_BASE_URL"]
        if "KAIRO_TEST_API_KEY" in os.environ:
            del os.environ["KAIRO_TEST_API_KEY"]

    def test_load_config_file(self):
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.active_model_profile, "other / other_model")
        self.assertEqual(config.active_provider, "other")
        self.assertEqual(config.active_model, "other_model")
        self.assertEqual(config.get_model_profile_names(), ["test / test_model", "other / other_model"])
        self.assertEqual(config.api_key, "other_key")
        self.assertEqual(config.base_url, "https://other.api.com")
        self.assertEqual(config.model, "other_model")
        self.assertEqual(config.models, ["test / test_model", "other / other_model"])
        self.assertEqual(config.temperature, 0.7)
        self.assertEqual(config.max_tokens, 2000)
        self.assertEqual(config.context_window, 32000)
        self.assertEqual(config.context_management["trigger_percent"], 75.0)
        self.assertEqual(config.context_management["target_percent"], 50.0)
        self.assertEqual(config.context_management["preserve_recent_turns"], 2)
        self.assertEqual(config.skills_dir, "./test_skills")
        self.assertTrue(config.auto_mode)
        self.assertTrue(config.plan_mode)
        self.assertTrue(config.thinking_mode)
        self.assertEqual(config.ui["dock_breakpoint"], 132)
        self.assertEqual(config.ui["dock_width_ratio"], 0.333)
        self.assertEqual(config.ui["dock_min_width"], 36)
        self.assertEqual(config.ui["dock_max_width"], 50)
        self.assertTrue(config.ui["workspace_enabled"])
        self.assertEqual(config.ui["workspace_refresh_seconds"], 2.0)
        self.assertEqual(config.ui["workspace_max_files"], 2000)
        self.assertEqual(config.ui["workspace_diff_max_bytes"], 204800)

    def test_env_overrides(self):
        os.environ["OPENAI_API_KEY"] = "env_key"
        os.environ["OPENAI_BASE_URL"] = "https://env.api.com"
        
        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.api_key, "env_key")
        self.assertEqual(config.base_url, "https://env.api.com")
        # should still read the active profile's model from json if not in env
        self.assertEqual(config.model, "other_model")

    def test_legacy_default_dock_width_migrates_for_workspace(self):
        self.default_data["ui"]["dock_width"] = 32
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.default_data, f)

        config = Config(config_path=str(self.config_path))

        self.assertEqual(config.ui["dock_max_width"], 64)
        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("dock_width", saved["ui"])

    def test_legacy_models_are_converted_to_profiles(self):
        legacy_data = {
            "api_key": "legacy_key",
            "base_url": "https://legacy.api.com",
            "model": "legacy_model",
            "models": ["legacy_model", "backup_model"],
            "temperature": 0.3,
            "max_tokens": 3000,
            "context_window": 96000
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)

        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.get_model_profile_names(), ["legacy_model / legacy_model", "backup_model / backup_model"])
        self.assertEqual(config.active_model_profile, "legacy_model / legacy_model")
        self.assertEqual(config.active_provider, "legacy_model")
        self.assertEqual(config.active_model, "legacy_model")
        self.assertEqual(config.model, "legacy_model")

    def test_profile_missing_api_key_uses_global_default(self):
        config = Config(config_path=str(self.config_path))
        self.assertTrue(config.apply_model_profile("test"))
        self.assertEqual(config.api_key, "test_key")
        self.assertEqual(config.context_management["trigger_percent"], 85.0)

        config.llm["providers"].append({
            "name": "fallback",
            "base_url": "https://fallback.api.com",
            "api_key": "test_key",
            "models": [
                {
                    "name": "fallback_model",
                    "temperature": 0.5,
                    "max_tokens": 1000,
                    "context_window": 64000,
                }
            ],
        })

        self.assertTrue(config.apply_model_profile("fallback"))
        self.assertEqual(config.api_key, "test_key")
        self.assertEqual(config.model, "fallback_model")
        self.assertEqual(config.base_url, "https://fallback.api.com")

    def test_profile_api_key_env_is_resolved_without_being_saved(self):
        os.environ["KAIRO_TEST_API_KEY"] = "runtime-secret"
        os.environ["OPENAI_API_KEY"] = "global-secret"
        self.default_data["api_key"] = ""
        self.default_data["active_model_profile"] = "test"
        self.default_data["model_profiles"][0].pop("api_key")
        self.default_data["model_profiles"][0]["api_key_env"] = "KAIRO_TEST_API_KEY"
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.default_data, f)

        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.api_key, "runtime-secret")

        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("api_key", saved)
        self.assertEqual(saved["llm"]["providers"][0]["api_key_env"], "KAIRO_TEST_API_KEY")
        self.assertNotIn("runtime-secret", json.dumps(saved))

    def test_new_llm_provider_structure_loads_and_saves(self):
        new_data = {
            "llm": {
                "active_provider": "deepseek",
                "active_model": "deepseek-chat",
                "defaults": {
                    "temperature": 0.2,
                    "max_tokens": 4000,
                    "context_window": 128000,
                },
                "providers": [
                    {
                        "name": "deepseek",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key": "deepseek_key",
                        "models": [
                            {
                                "name": "deepseek-chat",
                                "temperature": 0.3,
                                "max_tokens": 8000,
                                "context_window": 128000,
                            }
                        ],
                    }
                ],
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(new_data, f)

        config = Config(config_path=str(self.config_path))
        self.assertEqual(config.active_provider, "deepseek")
        self.assertEqual(config.active_model, "deepseek-chat")
        self.assertEqual(config.active_model_profile, "deepseek / deepseek-chat")
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.api_key, "deepseek_key")
        self.assertEqual(config.model, "deepseek-chat")

        config.save()
        with open(self.config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["llm"]["active_provider"], "deepseek")
        self.assertEqual(saved["llm"]["active_model"], "deepseek-chat")

if __name__ == "__main__":
    unittest.main()
