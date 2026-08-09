"""Settings page: profiles CRUD + role routing, keyring secrets, preferences,
theme/animation/keybindings, read-only workspace configuration, recent workspaces.

- **Profiles** are CRUDed through the public providers facade with the catalog
  revision read from ``providers.snapshot()`` (increments per mutation); Delete
  surfaces the kernel CONFLICT when a profile is still role-mapped.
- **Role routing** maps/unmaps a role to a catalog profile (``map_role`` /
  ``unmap_role`` with the expected revision).
- **Keyring secrets** use the TUI SecretStore only (``app._bootstrap.secret_store``):
  Store opens a password modal and never writes plaintext to the document;
  env-fallback mode stores only an environment reference. Safe mode raises
  ``SecretNotStored`` which is surfaced inline. Delete goes through
  ``providers.delete_secret`` (which refuses secrets still referenced by a
  profile) before the store delete.
- **Preferences** are patched on one Apply button; ``clear_profile_id=True`` is
  set when the profile override Select is "none". Safe mode forces Manual
  authorization. Context thresholds are validated numeric Inputs (1..100 and
  1..trigger — Textual 8.2.8 has no Slider).
- **Theme / animation / keybindings** are written to the ConfigDocument with
  untouched fields preserved (``dataclasses.replace``); the chosen theme is
  applied to the running Textual app immediately ("default" → "textual-dark").
  Keybindings are persisted to the document but not runtime-bound this phase.
- **Workspace configuration** is a read-only, redacted render of
  ``kernel.configuration.snapshot().values`` — the kernel ``ConfigPatch`` is not
  publicly constructible, so this section never patches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import cast

from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.json import thaw_json
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.contracts.providers import ProviderProfile
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static, Switch

from kairo_tui.config_document import ConfigDocumentAdapter
from kairo_tui.keyring_store import SecretNotStored
from kairo_tui.settings_model import provider_rows, role_rows
from kairo_tui.store import ConfigAction
from kairo_tui.workspace_model import change_button_id

THEME_ALIASES = {"default": "textual-dark"}


@dataclass(frozen=True)
class ProfileFormData:
    label: str
    provider: str
    model: str
    base_url: str
    context_window: int
    max_output_tokens: int
    temperature: float


@dataclass(frozen=True)
class _SecretRef:
    """Structural stand-in for the kernel's private ``SecretRef`` service DTO.

    ``delete_secret`` reads only ``.secret_id``; the TUI builds an
    attribute-compatible value instead of importing the forbidden module.
    """

    secret_id: SecretId


class ProfileFormModal(ModalScreen[ProfileFormData | None]):
    """Seven-field profile form; New is blank, Edit is prefilled."""

    def __init__(
        self,
        *,
        label: str = "",
        provider: str = "",
        model: str = "",
        base_url: str = "",
        context_window: str = "",
        max_output_tokens: str = "",
        temperature: str = "",
        editing: bool = False,
    ) -> None:
        super().__init__()
        self._label, self._provider, self._model = label, provider, model
        self._base_url = base_url
        self._context_window = context_window
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._editing = editing

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-form-modal"):
            yield Static("New provider profile" if not self._editing else "Edit provider profile", id="profile-form-title")
            yield Input(self._label, id="profile-form-label", placeholder="Label")
            yield Input(self._provider, id="profile-form-provider", placeholder="Provider (e.g. openai_responses)")
            yield Input(self._model, id="profile-form-model", placeholder="Model")
            yield Input(self._base_url, id="profile-form-base_url", placeholder="Base URL")
            yield Input(self._context_window, id="profile-form-context_window", placeholder="Context window")
            yield Input(self._max_output_tokens, id="profile-form-max_output_tokens", placeholder="Max output tokens")
            yield Input(self._temperature, id="profile-form-temperature", placeholder="Temperature")
            yield Static("", id="profile-form-error", markup=False)
            with Horizontal(id="profile-form-actions"):
                yield Button("Save", id="profile-form-save", variant="primary")
                yield Button("Cancel", id="profile-form-cancel")

    def _submit(self) -> None:
        try:
            context_window = int(self.query_one("#profile-form-context_window", Input).value)
            max_output_tokens = int(self.query_one("#profile-form-max_output_tokens", Input).value)
            temperature = float(self.query_one("#profile-form-temperature", Input).value)
        except ValueError as exc:
            self.query_one("#profile-form-error", Static).update(f"Numeric fields are required: {exc}")
            return
        data = ProfileFormData(
            self.query_one("#profile-form-label", Input).value.strip(),
            self.query_one("#profile-form-provider", Input).value.strip(),
            self.query_one("#profile-form-model", Input).value.strip(),
            self.query_one("#profile-form-base_url", Input).value.strip(),
            context_window,
            max_output_tokens,
            temperature,
        )
        if not data.label or not data.provider or not data.model or not data.base_url:
            self.query_one("#profile-form-error", Static).update("Label, provider, model and base URL are required.")
            return
        self.dismiss(data)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "profile-form-save":
            self._submit()
        elif event.button.id == "profile-form-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "profile-form-temperature":
            self._submit()


class SecretPasswordModal(ModalScreen[str | None]):
    """Password modal for one secret store; the value never leaves the store."""

    def __init__(self, secret_id: str) -> None:
        super().__init__()
        self._secret_id = secret_id

    def compose(self) -> ComposeResult:
        with Vertical(id="secret-password-modal"):
            yield Static(f"Store secret for {self._secret_id}", id="secret-password-title")
            yield Input(password=True, id="secret-password", placeholder="Secret value")
            with Horizontal(id="secret-password-actions"):
                yield Button("Store", id="secret-password-save", variant="primary")
                yield Button("Cancel", id="secret-password-cancel")

    def _submit(self) -> None:
        self.dismiss(self.query_one("#secret-password", Input).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "secret-password-save":
            self._submit()
        elif event.button.id == "secret-password-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "secret-password":
            self._submit()


class SettingsScreen(Container):
    """Settings page: seven sections in one scrolling column."""

    DEFAULT_CSS = """
    SettingsScreen { height: 1fr; }
    SettingsScreen #settings-scroll { height: 1fr; }
    SettingsScreen #settings-profiles-list, SettingsScreen #settings-secrets-list,
    SettingsScreen #settings-keybindings-list { height: auto; max-height: 10; }
    SettingsScreen #settings-roles-list { height: auto; max-height: 5; }
    SettingsScreen #settings-recent-list { height: auto; max-height: 5; }
    SettingsScreen #settings-config-json { height: auto; max-height: 10; }
    SettingsScreen Horizontal { height: auto; }
    SettingsScreen Input, SettingsScreen Select { width: 20; }
    SettingsScreen Button { min-width: 0; }
    SettingsScreen #settings-context-trigger, SettingsScreen #settings-context-target,
    SettingsScreen #settings-preserve-turns { width: 8; }
    SettingsScreen #settings-profiles-list Horizontal,
    SettingsScreen #settings-secrets-list Horizontal,
    SettingsScreen #settings-keybindings-list Horizontal { height: auto; }
    SettingsScreen #settings-profiles-list Static,
    SettingsScreen #settings-secrets-list Static,
    SettingsScreen #settings-keybindings-list Static { width: 1fr; }
    SettingsScreen #settings-profiles-list Button,
    SettingsScreen #settings-secrets-list Button,
    SettingsScreen #settings-keybindings-list Button { width: 9; }
    #profile-form-modal { width: 60; border: round $primary; background: $surface; padding: 1 2; }
    #secret-password-modal { width: 60; border: round $primary; background: $surface; padding: 1 2; }
    """

    def __init__(self, app) -> None:
        super().__init__(id="settings-screen")
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
        self._profile_ids: dict[str, str] = {}   # sanitized key → profile_id
        self._secret_ids: dict[str, str] = {}    # sanitized key → secret_id
        self._keybinding_rows: list[tuple[str, str]] = []  # (key_input_id, command_input_id)

    def compose(self) -> ComposeResult:
        yield Static("[b]Settings[/b]", id="settings-title")
        with VerticalScroll(id="settings-scroll"):
            yield Static("[b]Profiles[/b]", id="settings-profiles-label")
            yield VerticalScroll(id="settings-profiles-list")
            yield Button("New", id="settings-profile-new", variant="primary")
            yield Static("[b]Role routing[/b]", id="settings-roles-label")
            with Horizontal(id="settings-role-row"):
                yield Input(placeholder="Role", id="settings-role-input")
                yield Select([], id="settings-role-profile")
                yield Button("Map", id="settings-role-map", variant="primary")
                yield Button("Unmap", id="settings-role-unmap")
            yield VerticalScroll(id="settings-roles-list")
            yield Static("[b]Keyring secrets[/b]", id="settings-secrets-label")
            yield Static("", id="settings-secrets-status")
            yield VerticalScroll(id="settings-secrets-list")
            yield Static("[b]Authorization / Plan / Thinking / context[/b]", id="settings-preferences-label")
            with Horizontal(id="settings-auth-row"):
                yield Static("Authorization")
                yield Select(
                    [(mode.value.capitalize(), mode) for mode in AuthorizationMode],
                    value=AuthorizationMode.MANUAL,
                    id="settings-auth",
                )
                yield Static("Profile override")
                yield Select([], id="settings-profile-override")
            with Horizontal(id="settings-switches-row"):
                yield Static("Plan")
                yield Switch(id="settings-plan")
                yield Static("Thinking")
                yield Switch(value=True, id="settings-thinking")
            with Horizontal(id="settings-context-row"):
                yield Static("Trigger %")
                yield Input("85", id="settings-context-trigger")
                yield Static("Target %")
                yield Input("60", id="settings-context-target")
                yield Static("Preserve turns")
                yield Input("4", id="settings-preserve-turns")
            yield Button("Apply", id="settings-preferences-apply", variant="primary")
            yield Static("", id="settings-preferences-status")
            yield Static("[b]Theme / animation / keybindings[/b]", id="settings-appearance-label")
            with Horizontal(id="settings-theme-row"):
                yield Static("Theme")
                yield Select([("default", "default")], value="default", id="settings-theme")
            yield Checkbox("Reduced motion", id="settings-reduced-motion")
            with Horizontal(id="settings-keybinding-add-row"):
                yield Input(placeholder="Key (e.g. ctrl+k)", id="settings-keybinding-key")
                yield Input(placeholder="Command", id="settings-keybinding-command")
                yield Button("Add", id="settings-keybinding-add")
            yield VerticalScroll(id="settings-keybindings-list")
            yield Static("[b]Workspace configuration (read-only)[/b]", id="settings-config-label")
            yield Static("", id="settings-config-json", markup=False)
            yield Static("[b]Recent workspace[/b]", id="settings-recent-label")
            yield VerticalScroll(id="settings-recent-list")
        yield Static("", id="settings-status")

    def on_mount(self) -> None:
        self.run_worker(self._load())

    async def _load(self) -> None:
        await self._refresh_catalog()
        await self._refresh_preferences()
        await self._refresh_secrets()
        await self._refresh_config()
        await self._render_appearance()

    # --- Profiles ---

    async def _refresh_catalog(self) -> None:
        snapshot = await self.kernel.providers.snapshot()
        if not self.is_mounted:
            return
        await self._render_profiles(snapshot)
        await self._render_roles(snapshot)
        self._update_role_select(snapshot)
        self._update_override_select(snapshot)

    async def _render_profiles(self, snapshot) -> None:
        container = self.query_one_optional("#settings-profiles-list", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        self._profile_ids = {}
        rows = provider_rows(snapshot)
        if not rows:
            await container.mount(Static("No provider profiles.", id="settings-profiles-empty"))
            return
        for profile, label in zip(snapshot.profiles, rows, strict=False):
            key = change_button_id(str(profile.profile_id))
            self._profile_ids[key] = str(profile.profile_id)
            row = Horizontal(id=f"prof-row-{key}")
            await container.mount(row)
            row.mount(Static(label))
            row.mount(Button("Edit", id=f"prof-edit-{key}"))
            row.mount(Button("Delete", id=f"prof-delete-{key}", variant="error"))
            row.mount(Button("Probe", id=f"prof-probe-{key}"))

    async def _render_roles(self, snapshot) -> None:
        container = self.query_one_optional("#settings-roles-list", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        rows = role_rows(snapshot)
        if not rows:
            await container.mount(Static("No role mappings.", id="settings-roles-empty"))
            return
        for row in rows:
            await container.mount(Static(row))

    def _update_role_select(self, snapshot) -> None:
        select = self.query_one_optional("#settings-role-profile", Select)
        if select is None:
            return
        options = [(profile.label, str(profile.profile_id)) for profile in snapshot.profiles]
        select.set_options(options)
        if select.value not in [value for _, value in options]:
            select.value = select.NULL

    def _update_override_select(self, snapshot) -> None:
        select = self.query_one_optional("#settings-profile-override", Select)
        if select is None:
            return
        options = [("none", "none")] + [(profile.label, str(profile.profile_id)) for profile in snapshot.profiles]
        select.set_options(options)
        if select.value not in [value for _, value in options]:
            select.value = "none"

    async def _new_profile(self) -> None:
        data = await self._app.push_screen_wait(ProfileFormModal())
        if data is None:
            return
        profile = ProviderProfile(
            ProfileId(f"{data.provider}:{data.model}"),
            data.label,
            data.provider,
            data.model,
            data.base_url,
            data.context_window,
            data.max_output_tokens,
            data.temperature,
        )
        await self._create_profile(profile)

    async def _edit_profile(self, profile_id: ProfileId) -> None:
        snapshot = await self.kernel.providers.snapshot()
        profile = next((p for p in snapshot.profiles if p.profile_id == profile_id), None)
        if profile is None:
            return
        data = await self._app.push_screen_wait(ProfileFormModal(
            label=profile.label,
            provider=profile.provider,
            model=profile.model,
            base_url=profile.base_url,
            context_window=str(profile.context_window),
            max_output_tokens=str(profile.max_output_tokens),
            temperature=str(profile.temperature),
            editing=True,
        ))
        if data is None:
            return
        profile = ProviderProfile(
            profile_id,
            data.label,
            data.provider,
            data.model,
            data.base_url,
            data.context_window,
            data.max_output_tokens,
            data.temperature,
            secret_id=profile.secret_id,
        )
        await self._create_profile(profile, updating=True)

    async def _create_profile(self, profile: ProviderProfile, *, updating: bool = False) -> None:
        snapshot = await self.kernel.providers.snapshot()
        expected = int(getattr(snapshot, "revision", 0) or 0)
        if updating:
            result = await self.kernel.providers.update_profile(profile, expected)
        else:
            result = await self.kernel.providers.create_profile(profile, expected)
        if not result.ok:
            self._notice(result.error.message if result.error else "Profile save failed.")
            return
        self._notice("")
        await self._refresh_catalog()

    async def _delete_profile(self, profile_id: ProfileId) -> None:
        snapshot = await self.kernel.providers.snapshot()
        expected = int(getattr(snapshot, "revision", 0) or 0)
        result = await self.kernel.providers.delete_profile(profile_id, expected)
        if not result.ok:
            # CONFLICT (still role-mapped) and revision conflicts surface here.
            self._notice(result.error.message if result.error else "Profile delete failed.")
            return
        self._notice("")
        await self._refresh_catalog()

    async def _probe_profile(self, profile_id: ProfileId) -> None:
        result = await self.kernel.providers.probe(profile_id)
        if result.ok and result.value is not None:
            self._notice(f"Probe: {result.value.provider}/{result.value.model} is reachable.")
        else:
            # Informational: unreachable or no probe adapter. Explicit outcome.
            self._notice(f"Probe unavailable: {result.error.message if result.error else 'no result'} — informational.")

    # --- Role routing ---

    async def _map_role(self) -> None:
        role = self.query_one("#settings-role-input", Input).value.strip()
        select = self.query_one("#settings-role-profile", Select)
        if not role or select.value is select.NULL:
            self._notice("Role and profile are required.")
            return
        snapshot = await self.kernel.providers.snapshot()
        expected = int(getattr(snapshot, "revision", 0) or 0)
        result = await self.kernel.providers.map_role(role, ProfileId(str(select.value)), expected)
        if not result.ok:
            self._notice(result.error.message if result.error else "Role map failed.")
            return
        self.query_one("#settings-role-input", Input).value = ""
        self._notice("")
        await self._refresh_catalog()

    async def _unmap_role(self) -> None:
        role = self.query_one("#settings-role-input", Input).value.strip()
        if not role:
            self._notice("Role is required.")
            return
        snapshot = await self.kernel.providers.snapshot()
        expected = int(getattr(snapshot, "revision", 0) or 0)
        result = await self.kernel.providers.unmap_role(role, expected)
        if not result.ok:
            self._notice(result.error.message if result.error else "Role unmap failed.")
            return
        self._notice("")
        await self._refresh_catalog()

    # --- Keyring secrets ---

    async def _refresh_secrets(self) -> None:
        store = self._app._bootstrap.secret_store
        available, reason = store.available
        status = self.query_one_optional("#settings-secrets-status", Static)
        if status is not None:
            status.update(f"Backend: {'available' if available else reason}")
        snapshot = await self.kernel.providers.snapshot()
        if not self.is_mounted:
            return
        container = self.query_one_optional("#settings-secrets-list", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        self._secret_ids = {}
        if not snapshot.profiles:
            await container.mount(Static("No profiles.", id="settings-secrets-empty"))
            return
        for profile in snapshot.profiles:
            secret_id = profile.secret_id or str(profile.profile_id)
            reference = store.describe(SecretId(secret_id))
            masked = "********" if reference.present else "(not present)"
            key = change_button_id(secret_id)
            self._secret_ids[key] = secret_id
            row = Horizontal(id=f"sec-row-{key}")
            await container.mount(row)
            row.mount(Static(f"{profile.label} — {secret_id} — {reference.source} — {masked}"))
            row.mount(Button("Store", id=f"sec-store-{key}", variant="primary"))
            row.mount(Button("Delete", id=f"sec-delete-{key}", variant="error"))

    async def _store_secret(self, secret_id: str) -> None:
        value = await self._app.push_screen_wait(SecretPasswordModal(secret_id))
        if value is None:
            return
        try:
            self._app._bootstrap.secret_store.store(SecretId(secret_id), value)
        except SecretNotStored as exc:
            # Safe mode (or env fallback without the variable) surfaces here.
            self._notice(str(exc))
            return
        self._notice("")
        await self._refresh_secrets()

    async def _delete_secret(self, secret_id: str) -> None:
        result = await self.kernel.providers.delete_secret(_SecretRef(SecretId(secret_id)))
        if not result.ok:
            # CONFLICT when a profile still references the secret.
            self._notice(result.error.message if result.error else "Secret delete failed.")
            return
        self._app._bootstrap.secret_store.delete(SecretId(secret_id))
        self._notice("")
        await self._refresh_secrets()

    # --- Preferences ---

    async def _refresh_preferences(self) -> None:
        snapshot = await self.kernel.preferences.snapshot()
        if not self.is_mounted:
            return
        auth = self.query_one_optional("#settings-auth", Select)
        plan = self.query_one_optional("#settings-plan", Switch)
        thinking = self.query_one_optional("#settings-thinking", Switch)
        trigger = self.query_one_optional("#settings-context-trigger", Input)
        target = self.query_one_optional("#settings-context-target", Input)
        preserve = self.query_one_optional("#settings-preserve-turns", Input)
        override = self.query_one_optional("#settings-profile-override", Select)
        if auth is None or plan is None or thinking is None or trigger is None or target is None or preserve is None or override is None:
            return
        auth.value = snapshot.authorization_mode
        plan.value = snapshot.plan_mode
        thinking.value = snapshot.thinking_mode
        trigger.value = str(int(snapshot.context_trigger_percent))
        target.value = str(int(snapshot.context_target_percent))
        preserve.value = str(snapshot.preserve_recent_turns)
        override.value = str(snapshot.profile_id) if snapshot.profile_id is not None else "none"
        if self.store.state.safe_mode:
            # Safe mode forces Manual authorization.
            auth.disabled = True
            auth.value = AuthorizationMode.MANUAL

    async def _apply_preferences(self) -> None:
        status = self.query_one("#settings-preferences-status", Static)
        try:
            trigger = float(self.query_one("#settings-context-trigger", Input).value)
            target = float(self.query_one("#settings-context-target", Input).value)
            preserve = int(self.query_one("#settings-preserve-turns", Input).value)
        except ValueError as exc:
            status.update(f"Invalid number: {exc}")
            return
        if not 1.0 <= trigger <= 100.0:
            status.update("context_trigger_percent must be within 1..100.")
            return
        if not 1.0 <= target <= trigger:
            status.update("context_target_percent must be within 1..trigger.")
            return
        if preserve < 0:
            status.update("preserve_recent_turns cannot be negative.")
            return
        snapshot = await self.kernel.preferences.snapshot()
        override = self.query_one("#settings-profile-override", Select)
        clear_profile = override.value == "none"
        patch = PreferencesPatch(
            snapshot.revision,
            authorization_mode=cast(AuthorizationMode, self.query_one("#settings-auth", Select).value),
            plan_mode=self.query_one("#settings-plan", Switch).value,
            thinking_mode=self.query_one("#settings-thinking", Switch).value,
            context_trigger_percent=trigger,
            context_target_percent=target,
            preserve_recent_turns=preserve,
            clear_profile_id=clear_profile,
            profile_id=None if clear_profile else ProfileId(str(override.value)),
        )
        result = await self.kernel.preferences.patch(patch)
        if not result.ok:
            status.update(result.error.message if result.error else "Preferences patch failed.")
            return
        status.update("Preferences saved.")
        await self._refresh_preferences()

    # --- Theme / animation / keybindings ---

    async def _render_appearance(self) -> None:
        if not self.is_mounted:
            return
        theme = self.query_one_optional("#settings-theme", Select)
        if theme is None:
            return
        document = self.store.state.document
        names = sorted(self._app.available_themes)
        options = [("default", "default")] + [(name, name) for name in names]
        theme.set_options(options)
        theme.value = document.theme if document.theme in {name for _, name in options} else "default"
        motion = self.query_one_optional("#settings-reduced-motion", Checkbox)
        if motion is not None:
            motion.value = document.reduced_motion
        await self._render_keybindings(document.keybindings)
        await self._render_recent(document.recent_workspaces)

    def _apply_theme(self, name: str) -> None:
        target = THEME_ALIASES.get(name, name)
        if target not in self._app.available_themes:
            target = THEME_ALIASES["default"]
            self._notice(f"Theme '{name}' is not available; using textual-dark.")
        if self._app.theme != target:
            self._app.theme = target

    def _save_document(self, **changes: object) -> None:
        document = replace(self.store.state.document, **changes)
        ConfigDocumentAdapter(
            self._app._bootstrap.config_path, safe_mode=self.store.state.safe_mode
        ).save(document)
        self.store.dispatch(ConfigAction(document, self.store.state.setup_complete))

    async def _render_keybindings(self, keybindings: tuple[tuple[str, str], ...]) -> None:
        container = self.query_one_optional("#settings-keybindings-list", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        self._keybinding_rows = []
        for index, (key, command) in enumerate(keybindings):
            key_id, command_id = f"kb-key-{index}", f"kb-command-{index}"
            row = Horizontal(id=f"kb-row-{index}")
            await container.mount(row)
            row.mount(Input(key, id=key_id, placeholder="Key"))
            row.mount(Input(command, id=command_id, placeholder="Command"))
            row.mount(Button("Remove", id=f"kb-remove-{index}"))
            self._keybinding_rows.append((key_id, command_id))

    def _collect_keybindings(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for key_id, command_id in self._keybinding_rows:
            key = self.query_one(f"#{key_id}", Input).value.strip()
            command = self.query_one(f"#{command_id}", Input).value.strip()
            if key and command:
                pairs.append((key, command))
        return tuple(pairs)

    async def _add_keybinding(self) -> None:
        key = self.query_one("#settings-keybinding-key", Input).value.strip()
        command = self.query_one("#settings-keybinding-command", Input).value.strip()
        pairs = self._collect_keybindings()
        if key and command:
            self.query_one("#settings-keybinding-key", Input).value = ""
            self.query_one("#settings-keybinding-command", Input).value = ""
            pairs = pairs + ((key, command),)
            self._save_document(keybindings=pairs)
        await self._render_keybindings(pairs)

    async def _remove_keybinding(self, index: int) -> None:
        pairs = list(self._collect_keybindings())
        if 0 <= index < len(pairs):
            pairs.pop(index)
        self._save_document(keybindings=tuple(pairs))
        await self._render_keybindings(tuple(pairs))

    # --- Workspace configuration (read-only) ---

    async def _refresh_config(self) -> None:
        snapshot = await self.kernel.configuration.snapshot()
        if not self.is_mounted:
            return
        json_static = self.query_one_optional("#settings-config-json", Static)
        if json_static is not None:
            json_static.update(json.dumps(thaw_json(snapshot.values), indent=2, ensure_ascii=False))

    # --- Recent workspace ---

    async def _render_recent(self, recent: tuple[str, ...]) -> None:
        container = self.query_one_optional("#settings-recent-list", VerticalScroll)
        if container is None:
            return
        await container.remove_children()
        if not recent:
            await container.mount(Static("No recent workspaces.", id="settings-recent-empty"))
            return
        for path in recent:
            await container.mount(Static(path))

    # --- Widget events ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "settings-profile-new":
            self.run_worker(self._new_profile())
        elif button_id == "settings-role-map":
            self.run_worker(self._map_role())
        elif button_id == "settings-role-unmap":
            self.run_worker(self._unmap_role())
        elif button_id == "settings-preferences-apply":
            self.run_worker(self._apply_preferences())
        elif button_id == "settings-keybinding-add":
            self.run_worker(self._add_keybinding())
        elif button_id.startswith("prof-edit-"):
            profile_id = self._profile_ids.get(button_id.removeprefix("prof-edit-"), "")
            if profile_id:
                self.run_worker(self._edit_profile(ProfileId(profile_id)))
        elif button_id.startswith("prof-delete-"):
            profile_id = self._profile_ids.get(button_id.removeprefix("prof-delete-"), "")
            if profile_id:
                self.run_worker(self._delete_profile(ProfileId(profile_id)))
        elif button_id.startswith("prof-probe-"):
            profile_id = self._profile_ids.get(button_id.removeprefix("prof-probe-"), "")
            if profile_id:
                self.run_worker(self._probe_profile(ProfileId(profile_id)))
        elif button_id.startswith("sec-store-"):
            secret_id = self._secret_ids.get(button_id.removeprefix("sec-store-"), "")
            if secret_id:
                self.run_worker(self._store_secret(secret_id))
        elif button_id.startswith("sec-delete-"):
            secret_id = self._secret_ids.get(button_id.removeprefix("sec-delete-"), "")
            if secret_id:
                self.run_worker(self._delete_secret(secret_id))
        elif button_id.startswith("kb-remove-"):
            index = int(button_id.removeprefix("kb-remove-"))
            self.run_worker(self._remove_keybinding(index))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "settings-theme" or event.value is Select.NULL:
            return
        name = str(event.value)
        self._apply_theme(name)
        self._save_document(theme=name)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "settings-reduced-motion":
            self._save_document(reduced_motion=event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if (event.input.id or "").startswith("kb-"):
            self._save_document(keybindings=self._collect_keybindings())

    def _notice(self, message: str) -> None:
        status = self.query_one_optional("#settings-status", Static)
        if status is not None:
            status.update(message)
