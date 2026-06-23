"""Headless Textual tests for the 0.2.3 Settings/Provider/Model modals."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

try:
    from unittest import IsolatedAsyncioTestCase
except ImportError:  # pragma: no cover - very old Python fallback
    from asyncio import TestCase as IsolatedAsyncioTestCase  # type: ignore

if importlib.util.find_spec("textual") is None:
    raise unittest.SkipTest("textual is not installed in the current test environment")

if importlib.util.find_spec("textual") is None:
    raise unittest.SkipTest("textual is not installed in the current test environment")

from agent.config import Config
from agent.ui.app import KairoApp
from agent.ui.widgets import (
    Composer,
    ConnectionTestModal,
    ModelEditorModal,
    ProviderEditorModal,
    ProviderListModal,
    SettingsScreen,
)


def _seed_config(path: Path) -> None:
    data = {
        "llm": {
            "active_provider": "alpha",
            "active_model": "alpha-1",
            "defaults": {"temperature": 0.2, "max_tokens": 4000, "context_window": 32000},
            "providers": [
                {
                    "name": "alpha",
                    "base_url": "https://alpha.example.com/v1",
                    "api_key_env": "KAIRO_ALPHA_KEY",
                    "models": [
                        {"name": "alpha-1", "temperature": 0.2, "max_tokens": 4000, "context_window": 32000}
                    ],
                },
            ],
        },
        "sessions": {"enabled": True, "storage_dir": ".kairo/sessions"},
        "workspace_root": ".",
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


class TestSettingsModals(IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        config_path = self.root / "config.json"
        _seed_config(config_path)
        self.config = Config(config_path=str(config_path))
        from agent.bootstrap import build_registry
        registry = build_registry(self.config)
        # Point session storage somewhere harmless for these UI-only tests.
        self.config.sessions["storage_dir"] = str(self.root / "sessions")
        app = KairoApp(self.config, registry, animation=False, reduced_motion=True)
        app.config.config_path = Path(config_path)
        self.app = app

    def tearDown(self):
        self.dir.cleanup()

    async def test_settings_screen_opens_and_dismisses(self):
        async with self.app.run_test(size=(120, 35)) as pilot:
            await self.app.handle_command("/settings")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, SettingsScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, SettingsScreen)

    async def test_provider_add_modal_form_dismisses_with_values(self):
        async with self.app.run_test(size=(120, 35)) as pilot:
            await self.app.handle_command("/provider add")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ProviderEditorModal)
            await pilot.press("enter")
            await pilot.pause()
            self.assertFalse(isinstance(self.app.screen, ProviderEditorModal))

    async def test_provider_edit_opens_list_then_modal(self):
        async with self.app.run_test(size=(120, 35)) as pilot:
            await self.app.handle_command("/provider edit")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ProviderListModal)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ProviderEditorModal)

    async def test_connection_test_modal_dismisses(self):
        from agent.provider_health import ProviderTestResult

        result = ProviderTestResult(status="success", http_status=200, provider_message="ok", elapsed_ms=12)
        async with self.app.run_test(size=(120, 35)) as pilot:
            self.app.push_screen(ConnectionTestModal(result.summary(), result.ok))
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ConnectionTestModal)
            await pilot.press("enter")
            await pilot.pause()
            self.assertNotIsInstance(self.app.screen, ConnectionTestModal)

    async def test_model_add_opens_provider_list_then_form(self):
        async with self.app.run_test(size=(120, 35)) as pilot:
            await self.app.handle_command("/model add")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ProviderListModal)
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, ModelEditorModal)


if __name__ == "__main__":
    unittest.main()