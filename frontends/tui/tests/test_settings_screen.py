"""SettingsScreen pilot tests: profiles CRUD + role routing, keyring secrets,
preferences (authorization / plan / thinking / context + clear_profile_id), and
the recent-workspace record on a Workspace move.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_memory_screen.py
and test_workspace_screen.py.

Ratified deviation from the task-6 brief (see .superpowers/sdd/wb-task-6-report.md):
the "probe returns reachable" expectation cannot hold through the public facade:
``ProviderService.probe`` routes through probe adapters registered by the
factory only for the real router, so an injected FakeProvider yields
NOT_FOUND "Provider adapter is not available." (the same deterministic path
test_setup_screen.py ratifies). The probe test therefore asserts the surfaced
kernel result instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from textual.widgets import Button, Input, Select, Static, Switch

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.settings import ProfileFormModal, SecretPasswordModal
from kairo_tui.workspace_model import change_button_id
from tests.support.fakes import NOW_PROFILE, FakeProvider

# Env-reference fallback for the keyring-less test store: the seeded profile's
# effective secret id is "fake/model" -> KAIRO_SECRET_FAKE_MODEL.
_SECRET_ENV = "KAIRO_SECRET_FAKE_MODEL"


@pytest.fixture
def settings_app_factory(workspace: Path):
    """A booted KairoTuiApp on the Chat page with a seeded config + fake provider."""

    def make(*, provider=None, secret_store=None, safe_mode: bool = False) -> KairoTuiApp:
        document = ConfigDocument(
            profiles=(NOW_PROFILE,),
            roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
            default_profile_id=NOW_PROFILE.profile_id,
        )
        ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
        bootstrap = build_running_kernel(
            BootstrapOptions(
                workspace_root=str(workspace),
                config_path=workspace.parent / "config-v1.json",
                safe_mode=safe_mode,
            ),
            secret_store=secret_store or SecretStore(None),
            provider=provider or FakeProvider(),
        )
        return KairoTuiApp(bootstrap)
    return make


async def _wait_for(pilot, predicate, *, polls: int = 80, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


async def _wait_for_prefs(pilot, app: KairoTuiApp, predicate) -> None:
    """Poll the live preferences snapshot until ``predicate`` holds."""
    for _ in range(80):
        await pilot.pause(0.05)
        if predicate(await app.kernel.preferences.snapshot()):
            return


async def _wait_for_catalog(pilot, app: KairoTuiApp, predicate) -> None:
    """Poll the live provider catalog snapshot until ``predicate`` holds."""
    for _ in range(80):
        await pilot.pause(0.05)
        if predicate(await app.kernel.providers.snapshot()):
            return


async def _open_settings(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+6")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#settings-screen") is not None)


def _status_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#settings-status", Static).content)


def _profiles_text(app: KairoTuiApp) -> str:
    rows = app.query_one("#settings-profiles-list")
    return " ".join(str(item.content) for item in rows.query(Static))


def _secrets_text(app: KairoTuiApp) -> str:
    rows = app.query_one("#settings-secrets-list")
    return " ".join(str(item.content) for item in rows.query(Static))


def _button_ids(app: KairoTuiApp, container_id: str) -> set[str]:
    container = app.query_one(container_id)
    return {button.id or "" for button in container.query(Button)}


async def _fill_profile_form(pilot, app: KairoTuiApp, modal: ProfileFormModal) -> None:
    modal.query_one("#profile-form-label", Input).value = "OpenAI / GPT"
    modal.query_one("#profile-form-provider", Input).value = "openai_responses"
    modal.query_one("#profile-form-model", Input).value = "gpt-5.2"
    modal.query_one("#profile-form-base_url", Input).value = "https://api.openai.com/v1"
    modal.query_one("#profile-form-context_window", Input).value = "32000"
    modal.query_one("#profile-form-max_output_tokens", Input).value = "1000"
    modal.query_one("#profile-form-temperature", Input).value = "0.2"
    await pilot.click("#profile-form-save")
    await _wait_for(pilot, lambda: not isinstance(app.screen, ProfileFormModal))


def test_profiles_list_renders_seeded_profile(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "fake/model" in _profiles_text(app))
            assert "Fake / model" in _profiles_text(app)

    asyncio.run(drive())


def test_create_profile_appears_in_catalog(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await pilot.click("#settings-profile-new")
            await _wait_for(pilot, lambda: isinstance(app.screen, ProfileFormModal))
            await _fill_profile_form(pilot, app, cast(ProfileFormModal, app.screen))
            snapshot = await app.kernel.providers.snapshot()
            assert len(snapshot.profiles) == 2
            created = next(
                p for p in snapshot.profiles if p.profile_id == ProfileId("openai_responses:gpt-5.2")
            )
            assert created.label == "OpenAI / GPT"
            assert created.model == "gpt-5.2"
            # The new profile is unmapped; the seeded "chat" mapping is untouched.
            assert [(m.role, m.profile_id) for m in snapshot.roles] == [("chat", NOW_PROFILE.profile_id)]
            await _wait_for(pilot, lambda: "openai_responses:gpt-5.2" in _profiles_text(app))

    asyncio.run(drive())


def test_edit_profile_updates_it(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "prof-edit-fake-model" in _button_ids(app, "#settings-profiles-list"))
            await pilot.click("#prof-edit-fake-model")
            await _wait_for(pilot, lambda: isinstance(app.screen, ProfileFormModal))
            modal = cast(ProfileFormModal, app.screen)
            # Prefilled with the original values.
            assert modal.query_one("#profile-form-label", Input).value == "Fake / model"
            assert modal.query_one("#profile-form-model", Input).value == "model"
            modal.query_one("#profile-form-label", Input).value = "Fake / model (renamed)"
            modal.query_one("#profile-form-model", Input).value = "model-v2"
            await pilot.click("#profile-form-save")
            await _wait_for(pilot, lambda: not isinstance(app.screen, ProfileFormModal))
            snapshot = await app.kernel.providers.snapshot()
            updated = next(p for p in snapshot.profiles if p.profile_id == NOW_PROFILE.profile_id)
            assert updated.label == "Fake / model (renamed)"
            assert updated.model == "model-v2"
            assert updated.profile_id == NOW_PROFILE.profile_id  # identity is immutable

    asyncio.run(drive())


def test_delete_profile_removes_it_and_surfaces_conflict_when_mapped(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "prof-delete-fake-model" in _button_ids(app, "#settings-profiles-list"))
            # The seeded profile is still role-mapped: the kernel refuses with
            # CONFLICT and the message is surfaced inline, never swallowed.
            await pilot.click("#prof-delete-fake-model")
            await _wait_for(pilot, lambda: "role" in _status_text(app).casefold())
            snapshot = await app.kernel.providers.snapshot()
            assert len(snapshot.profiles) == 1
            # A new unmapped profile deletes cleanly.
            await pilot.click("#settings-profile-new")
            await _wait_for(pilot, lambda: isinstance(app.screen, ProfileFormModal))
            await _fill_profile_form(pilot, app, cast(ProfileFormModal, app.screen))
            key = change_button_id("openai_responses:gpt-5.2")
            await _wait_for(pilot, lambda: f"prof-delete-{key}" in _button_ids(app, "#settings-profiles-list"))
            await pilot.click(f"#prof-delete-{key}")
            await _wait_for_catalog(pilot, app, lambda s: len(s.profiles) == 1)
            snapshot = await app.kernel.providers.snapshot()
            assert [p.profile_id for p in snapshot.profiles] == [NOW_PROFILE.profile_id]

    asyncio.run(drive())


def test_probe_surfaces_kernel_result(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "prof-probe-fake-model" in _button_ids(app, "#settings-profiles-list"))
            await pilot.click("#prof-probe-fake-model")
            # The injected FakeProvider has no probe adapter registered (the
            # factory registers RouterProbe only for the real router), so the
            # kernel probe fails deterministically; the message is surfaced.
            await _wait_for(pilot, lambda: "Probe unavailable" in _status_text(app),
                            polls=200, delay=0.05)
            assert "not available" in _status_text(app).casefold()
            assert len((await app.kernel.providers.snapshot()).profiles) == 1  # untouched

    asyncio.run(drive())


def test_role_map_then_unmap(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: app.query_one_optional("#settings-role-profile", Select) is not None)
            app.query_one("#settings-role-input", Input).value = "chat2"
            app.query_one("#settings-role-profile", Select).value = str(NOW_PROFILE.profile_id)
            await pilot.click("#settings-role-map")
            await _wait_for_catalog(pilot, app, lambda s: any(m.role == "chat2" for m in s.roles))
            snapshot = await app.kernel.providers.snapshot()
            assert any(m.role == "chat2" and m.profile_id == NOW_PROFILE.profile_id for m in snapshot.roles)
            # Map clears the role input; re-enter it for the Unmap step.
            app.query_one("#settings-role-input", Input).value = "chat2"
            await pilot.click("#settings-role-unmap")
            await _wait_for_catalog(pilot, app, lambda s: all(m.role != "chat2" for m in s.roles))
            snapshot = await app.kernel.providers.snapshot()
            assert [(m.role, m.profile_id) for m in snapshot.roles] == [("chat", NOW_PROFILE.profile_id)]

    asyncio.run(drive())


def test_secret_store_shows_present_with_masked_value(settings_app_factory, monkeypatch) -> None:
    monkeypatch.setenv(_SECRET_ENV, "sk-test")
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "sec-store-fake-model" in _button_ids(app, "#settings-secrets-list"))
            await pilot.click("#sec-store-fake-model")
            await _wait_for(pilot, lambda: isinstance(app.screen, SecretPasswordModal))
            modal = cast(SecretPasswordModal, app.screen)
            modal.query_one("#secret-password", Input).value = "sk-test"
            await pilot.click("#secret-password-save")
            await _wait_for(pilot, lambda: not isinstance(app.screen, SecretPasswordModal))
            reference = app._bootstrap.secret_store.describe(SecretId("fake/model"))
            assert reference.present
            assert reference.source == "env"
            await _wait_for(pilot, lambda: "********" in _secrets_text(app))

    asyncio.run(drive())


def test_secret_delete_removes_it(settings_app_factory, monkeypatch) -> None:
    monkeypatch.setenv(_SECRET_ENV, "sk-test")
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            await _wait_for(pilot, lambda: "sec-delete-fake-model" in _button_ids(app, "#settings-secrets-list"))
            await pilot.click("#sec-delete-fake-model")
            await _wait_for(pilot, lambda: not app._bootstrap.secret_store.describe(SecretId("fake/model")).present)
            reference = app._bootstrap.secret_store.describe(SecretId("fake/model"))
            assert not reference.present
            await _wait_for(pilot, lambda: "not present" in _secrets_text(app))

    asyncio.run(drive())


def test_authorization_select_patches_preferences(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            select = app.query_one("#settings-auth", Select)
            await _wait_for(pilot, lambda: select.value is not None and str(select.value) != "")
            select.value = AuthorizationMode.AUTO
            await pilot.click("#settings-preferences-apply")
            await _wait_for_prefs(pilot, app, lambda s: s.authorization_mode is AuthorizationMode.AUTO)
            assert (await app.kernel.preferences.snapshot()).authorization_mode is AuthorizationMode.AUTO

    asyncio.run(drive())


def test_plan_thinking_switches_patch_preferences(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            plan = app.query_one("#settings-plan", Switch)
            thinking = app.query_one("#settings-thinking", Switch)
            await _wait_for(pilot, lambda: plan.value is False)
            plan.value = True
            thinking.value = False
            await pilot.click("#settings-preferences-apply")
            await _wait_for_prefs(pilot, app, lambda s: s.plan_mode and not s.thinking_mode)
            snapshot = await app.kernel.preferences.snapshot()
            assert snapshot.plan_mode is True
            assert snapshot.thinking_mode is False

    asyncio.run(drive())


def test_context_inputs_patch_preferences(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            app.query_one("#settings-context-trigger", Input).value = "90"
            app.query_one("#settings-context-target", Input).value = "50"
            app.query_one("#settings-preserve-turns", Input).value = "8"
            await pilot.click("#settings-preferences-apply")
            await _wait_for_prefs(
                pilot, app,
                lambda s: s.context_trigger_percent == 90.0
                and s.context_target_percent == 50.0
                and s.preserve_recent_turns == 8,
            )
            snapshot = await app.kernel.preferences.snapshot()
            assert snapshot.context_trigger_percent == 90.0
            assert snapshot.context_target_percent == 50.0
            assert snapshot.preserve_recent_turns == 8

    asyncio.run(drive())


def test_clear_profile_id_clears_runtime_profile(settings_app_factory) -> None:
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            # The seeded document's default_profile_id is the runtime override.
            assert (await app.kernel.preferences.snapshot()).profile_id == NOW_PROFILE.profile_id
            await _open_settings(pilot, app)
            override = app.query_one("#settings-profile-override", Select)
            await _wait_for(pilot, lambda: str(override.value) == str(NOW_PROFILE.profile_id))
            override.value = "none"
            await pilot.click("#settings-preferences-apply")
            await _wait_for_prefs(pilot, app, lambda s: s.profile_id is None)
            assert (await app.kernel.preferences.snapshot()).profile_id is None

    asyncio.run(drive())


def test_workspace_move_records_recent_workspace(settings_app_factory, workspace: Path) -> None:
    second = workspace.parent / "second-workspace"
    second.mkdir()
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            assert app.query_one_optional("#settings-recent-list") is not None
            await pilot.press("ctrl+3")
            await _wait_for(pilot, lambda: app.query_one_optional("#workspace-screen") is not None)
            app.query_one("#workspace-switch-target", Input).value = str(second)
            await pilot.click("#workspace-move")
            await _wait_for(pilot, lambda: app.store.state.workspace_root == str(second.resolve()))
            assert app.store.state.document.recent_workspaces == (str(second.resolve()),)
            # Returning to Settings renders the recorded workspace in the list.
            await pilot.press("ctrl+6")
            await _wait_for(pilot, lambda: app.query_one_optional("#settings-screen") is not None)
            await _wait_for(pilot, lambda: str(second.resolve()) in _recent_text(app))

    asyncio.run(drive())


def test_theme_select_applies_app_theme(settings_app_factory) -> None:
    """Choosing a theme in Settings applies `app.theme` immediately; the
    `"default"` alias maps to `"textual-dark"`."""
    app = settings_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_settings(pilot, app)
            theme = app.query_one("#settings-theme", Select)
            # Wait for _load to finish rendering the appearance options (8.2.8
            # Select keeps options in _options; no public reader).
            await _wait_for(pilot, lambda: "textual-light" in {str(value) for _, value in theme._options})
            theme.value = "textual-light"
            await pilot.pause()
            assert app.theme == "textual-light"
            assert app.store.state.document.theme == "textual-light"
            theme.value = "default"
            await pilot.pause()
            assert app.theme == "textual-dark"
            assert app.store.state.document.theme == "default"

    asyncio.run(drive())


def _recent_text(app: KairoTuiApp) -> str:
    rows = app.query_one("#settings-recent-list")
    return " ".join(str(item.content) for item in rows.query(Static))
