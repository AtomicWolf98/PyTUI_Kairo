"""Setup page: default page, sequential steps, send gating.

Ratified deviations from the task-9 brief (see .superpowers/sdd/tui-task-9-report.md):
- Tests use the repo's sync-body + ``asyncio.run(drive())`` pattern
  (test_app_layout.py): pytest-asyncio auto-mode rejects the nested
  ``asyncio.run`` inside ``build_running_kernel``.
- ``Static`` has no ``renderable`` attribute on textual>=8.2, so the body text
  is read through ``content``.
- Test 3 pre-sets the env-reference fallback variable so the keyring-less
  ``SecretStore(None)`` can resolve the stored secret (env mode only ever holds
  a reference, never plaintext).
- The app kernel is built with the TUI-local ``FakeProvider`` (public
  ProviderPort), so the probe step is hermetic: ``kernel.providers.probe``
  fails fast with "Provider adapter is not available." instead of calling
  api.openai.com — the deterministic continue-anyway path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import PageId
from tests.support.fakes import NOW_PROFILE, FakeProvider

# Env-reference fallback for the keyring-less test store: profile_id is
# "openai_responses:gpt-5.2" -> KAIRO_SECRET_OPENAI_RESPONSES_GPT_5_2.
_SETUP_SECRET_ENV = "KAIRO_SECRET_OPENAI_RESPONSES_GPT_5_2"


@pytest.fixture
def setup_app(workspace: Path):
    def make(*, theme: str | None = None) -> KairoTuiApp:
        # Hermetic probe: the fake provider answers the port, and because the
        # factory skips the real router (and its probe adapters) when a provider
        # is injected, the probe step never touches the network.
        bootstrap = build_running_kernel(
            BootstrapOptions(
                workspace_root=str(workspace),
                config_path=workspace.parent / "config-v1.json",
                theme=theme,
            ),
            secret_store=SecretStore(None),
            provider=FakeProvider(),
        )
        return KairoTuiApp(bootstrap)
    return make


def test_persist_rebuild_preserves_document_fields() -> None:
    """The `_persist` rebuild (dataclasses.replace on the live document) keeps
    theme / keybindings / recent_workspaces and only replaces the catalog
    fields the setup step legitimately owns."""
    from dataclasses import replace

    document = ConfigDocument(
        theme="nord",
        keybindings=(("ctrl+k", "command_palette"),),
        recent_workspaces=("/tmp/a",),
    )
    rebuilt = replace(
        document,
        profiles=(NOW_PROFILE,),
        roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
        default_profile_id=NOW_PROFILE.profile_id,
    )
    assert rebuilt.theme == "nord"
    assert rebuilt.keybindings == (("ctrl+k", "command_palette"),)
    assert rebuilt.recent_workspaces == ("/tmp/a",)
    assert rebuilt.profiles == (NOW_PROFILE,)
    assert rebuilt.roles == (RoleMapping("chat", NOW_PROFILE.profile_id),)
    assert rebuilt.default_profile_id == NOW_PROFILE.profile_id


def test_setup_completion_keeps_theme_in_document(setup_app, monkeypatch) -> None:
    """Completing setup after a `--theme nord` bootstrap must not wipe the
    document theme: the persisted file and the live document both keep it."""
    monkeypatch.setenv(_SETUP_SECRET_ENV, "sk-test-key")
    app = setup_app(theme="nord")
    assert app.store.state.document.theme == "nord"

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            from kairo_tui.screens.setup import SetupScreen

            screen = app.query_one(SetupScreen)
            await pilot.click("#setup-next")  # workspace → provider
            await pilot.pause()
            screen.query_one("#field-model", Input).value = "gpt-5.2"
            screen.query_one("#field-api_key", Input).value = "sk-test-key"
            await pilot.click("#setup-next")  # provider → keyring (creates profile + secret)
            await pilot.pause()
            # The _persist rebuild kept the bootstrap theme.
            assert app.store.state.document.theme == "nord"
            await pilot.click("#setup-next")  # keyring → probe
            await pilot.pause()
            await pilot.click("#setup-next")  # probe (informational)
            await pilot.pause()
            await pilot.click("#setup-next")  # permissions → finish
            await pilot.pause()
            assert app.store.state.setup_complete is True
            assert app.store.state.document.theme == "nord"
            # The persisted config file carries the theme too.
            saved = ConfigDocumentAdapter(app._bootstrap.config_path).load()
            assert saved.theme == "nord"
            assert saved.default_profile_id == app.store.state.document.default_profile_id

    asyncio.run(drive())


def test_empty_config_shows_setup_and_disables_send(setup_app) -> None:
    app = setup_app()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.store.state.page is PageId.SETUP
            assert app.query_one("#composer").disabled is True

    asyncio.run(drive())


def test_setup_step_sequencing(setup_app) -> None:
    app = setup_app()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            from kairo_tui.screens.setup import SetupScreen

            screen = app.query_one(SetupScreen)
            assert "1/5" in str(screen.query_one("#setup-body").content)
            await pilot.click("#setup-next")
            await pilot.pause()
            assert "2/5" in str(screen.query_one("#setup-body").content)
            await pilot.click("#setup-back")
            await pilot.pause()
            assert "1/5" in str(screen.query_one("#setup-body").content)

    asyncio.run(drive())


def test_setup_creates_profile_secret_and_completes(setup_app, monkeypatch) -> None:
    monkeypatch.setenv(_SETUP_SECRET_ENV, "sk-test-key")
    app = setup_app()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            from kairo_tui.screens.setup import SetupScreen

            screen = app.query_one(SetupScreen)
            # Walk to the provider step and fill the form.
            await pilot.click("#setup-next")  # workspace → provider
            await pilot.pause()
            screen.query_one("#field-model", Input).value = "gpt-5.2"
            screen.query_one("#field-api_key", Input).value = "sk-test-key"
            await pilot.click("#setup-next")  # provider → keyring (creates profile + secret)
            await pilot.pause()
            snapshot = await app.kernel.providers.snapshot()
            assert len(snapshot.profiles) == 1
            assert snapshot.profiles[0].secret_id != ""
            # Keyring → probe → permissions.
            await pilot.click("#setup-next")
            await pilot.pause()
            await pilot.click("#setup-next")  # probe (informational)
            await pilot.pause()
            await pilot.click("#setup-next")  # permissions → finish
            await pilot.pause()
            assert app.store.state.setup_complete is True
            assert app.query_one("#composer").disabled is False
            assert app.store.state.page is PageId.CHAT

    asyncio.run(drive())
