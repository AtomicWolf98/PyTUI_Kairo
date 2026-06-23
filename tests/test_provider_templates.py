"""Tests for provider templates (Feature 4)."""
from __future__ import annotations

import unittest

from agent.provider_templates import all_templates, get_template, list_templates


class TestProviderTemplates(unittest.TestCase):
    def test_expected_templates_present(self):
        names = list_templates()
        self.assertIn("OpenAI", names)
        self.assertIn("DeepSeek", names)
        self.assertIn("MiniMax", names)
        self.assertIn("Moonshot / Kimi", names)
        self.assertIn("Qwen compatible", names)
        self.assertIn("OpenRouter", names)
        self.assertIn("Local OpenAI-compatible", names)
        self.assertIn("Custom", names)

    def test_template_provides_default_provider_dict(self):
        openai = get_template("OpenAI")
        self.assertIsNotNone(openai)
        as_dict = openai.as_default_provider_dict()
        self.assertEqual(as_dict["name"], "openai")
        self.assertTrue(as_dict["base_url"].startswith("https://"))
        self.assertNotIn("api_key", as_dict)  # No raw keys embedded in templates.
        self.assertGreater(len(as_dict["models"]), 0)
        for model in as_dict["models"]:
            self.assertGreater(model["context_window"], 0)
            self.assertGreater(model["max_tokens"], 0)

    def test_custom_template_has_empty_fields(self):
        custom = get_template("Custom")
        self.assertEqual(custom.base_url, "")
        self.assertEqual(custom.api_key_env, "")
        self.assertEqual(custom.models, [])

    def test_all_templates_have_env_names_except_custom(self):
        for name, template in all_templates().items():
            if name == "Custom":
                continue
            self.assertTrue(template.api_key_env, msg=f"{name} missing api_key_env")

    def test_no_template_embeds_raw_api_key(self):
        for template in all_templates().values():
            as_dict = template.as_default_provider_dict()
            self.assertNotIn("api_key", as_dict)


if __name__ == "__main__":
    unittest.main()