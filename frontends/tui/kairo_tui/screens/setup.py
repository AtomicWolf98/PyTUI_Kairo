"""Setup page: sequential workspace / provider+secret / keyring / probe / permissions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SecretInput
from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, Static

from kairo_tui.config_document import ConfigDocumentAdapter, RoleMapping
from kairo_tui.store import ConfigAction, PageAction, PageId


class SetupScreen(Container):
    """Sequential configuration; the app disables sending until completion."""

    STEPS = ("workspace", "provider", "keyring", "probe", "permissions")
    FORM_FIELDS = ("provider", "model", "base_url", "api_key", "context_window", "max_output_tokens", "temperature")
    FORM_DEFAULTS = {
        "provider": "openai_responses", "model": "", "base_url": "https://api.openai.com/v1",
        "api_key": "", "context_window": "32000", "max_output_tokens": "1000", "temperature": "0.2",
    }

    def __init__(self, app) -> None:
        super().__init__(id="setup-screen")
        # Widget.app is a read-only Textual property, so the host app reference
        # lives at _app (same convention as the Task 8 skeleton).
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
        self._step_index = 0
        self._profile_id: ProfileId | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b]Kairo Setup[/b]", id="setup-title")
        yield Static("", id="setup-body")
        with Horizontal(id="setup-controls"):
            yield Button("Back", id="setup-back", variant="default")
            yield Button("Next", id="setup-next", variant="primary")

    def on_mount(self) -> None:
        # Textual's Button skips presses while the -active press animation is
        # running (rapid clicks get silently dropped); disable the animation so
        # every click registers deterministically in tests and live use.
        self.query_one("#setup-next", Button).active_effect_duration = 0
        self.query_one("#setup-back", Button).active_effect_duration = 0
        self._render_step()

    def _render_step(self) -> None:
        body = self.query_one("#setup-body", Static)
        step = self.STEPS[self._step_index]
        if step == "provider":
            body.update(self._step_text())
            self._mount_form(body)
        else:
            self._unmount_form(body)
            body.update(self._step_text())
        self.query_one("#setup-back", Button).disabled = self._step_index == 0
        self.query_one("#setup-next", Button).disabled = False

    def _mount_form(self, body: Static) -> None:
        """Mount the provider form Inputs into the body (idempotent)."""
        if body.query_one_optional("#field-provider", Input) is not None:
            return
        for name in self.FORM_FIELDS:
            body.mount(Input(value=self.FORM_DEFAULTS[name], placeholder=name, id=f"field-{name}"))

    def _unmount_form(self, body: Static) -> None:
        body.query(Input).remove()

    def _form_markup(self) -> str:
        defaults = {
            "provider": "openai_responses", "model": "", "base_url": "https://api.openai.com/v1",
            "api_key": "", "context_window": "32000", "max_output_tokens": "1000", "temperature": "0.2",
        }
        return "\n".join(f"{name}: {defaults[name]}" for name in self.FORM_FIELDS)

    def _field(self, name: str) -> str:
        try:
            return str(self.query_one(f"#field-{name}", Input).value)
        except Exception:
            return ""

    def _notice(self, text: str) -> None:
        self.query_one("#setup-body", Static).update(text)

    def _step_text(self) -> str:
        step = self.STEPS[self._step_index]
        if step == "workspace":
            return f"[1/5] Workspace\n{self.store.state.workspace_root or Path.cwd()}"
        if step == "provider":
            return "[2/5] Provider & model (creates the keyring secret and profile)"
        if step == "keyring":
            available, reason = self._app._bootstrap.secret_store.available
            return f"[3/5] Keyring backend: {'available' if available else reason}"
        if step == "probe":
            return "[4/5] Provider probe (informational)"
        return "[5/5] Permissions (authorization mode)"

    @on(Button.Pressed, "#setup-next")
    async def _next(self) -> None:
        step = self.STEPS[self._step_index]
        if step == "provider" and not await self._create_profile_from_form():
            return
        if step == "probe":
            await self._run_probe()
        if step == "permissions":
            await self._finish()
            return
        self._step_index += 1
        self._render_step()

    @on(Button.Pressed, "#setup-back")
    def _back(self) -> None:
        self._step_index = max(0, self._step_index - 1)
        self._render_step()

    async def _create_profile_from_form(self) -> bool:
        provider = self._field("provider") or "openai_responses"
        model = self._field("model")
        base_url = self._field("base_url") or "https://api.openai.com/v1"
        api_key = self._field("api_key")
        context_window = int(self._field("context_window") or "32000")
        max_output_tokens = int(self._field("max_output_tokens") or "1000")
        temperature = float(self._field("temperature") or "0.2")
        if not model:
            self._notice("Model is required.")
            return False
        profile_id = ProfileId(f"{provider}:{model}")
        secret_id = SecretId(str(profile_id))
        if api_key:
            stored = await self.kernel.providers.store_secret(SecretInput(secret_id, api_key))
            if not stored.ok:
                self._notice(f"Secret could not be stored: {stored.error.message}")
                return False
        profile = ProviderProfile(
            profile_id, f"{provider} / {model}", provider, model, base_url,
            context_window, max_output_tokens, temperature, secret_id=str(secret_id),
        )
        snapshot = await self.kernel.providers.snapshot()
        expected = int(getattr(snapshot, "revision", 0) or 0)
        created = await self.kernel.providers.create_profile(profile, expected)
        if not created.ok:
            self._notice(f"Profile could not be created: {created.error.message}")
            return False
        if not self.store.state.safe_mode:
            await self.kernel.providers.map_role("chat", profile_id, expected + 1)
        self._profile_id = profile_id
        await self._persist(profile_id)
        self._notice("Provider profile created.")
        return True

    async def _persist(self, profile_id: ProfileId) -> None:
        """Rebuild the document from the live catalog (authoritative after
        create_profile) and save it atomically; no-op in safe mode.

        ``replace`` keeps the document's display/appearance fields (theme,
        keybindings, recent_workspaces) intact — only the catalog fields the
        setup step owns are swapped in."""
        snapshot = await self.kernel.providers.snapshot()
        profiles = tuple(snapshot.profiles)
        document = replace(
            self.store.state.document,
            profiles=profiles,
            roles=(RoleMapping("chat", profile_id),),
            default_profile_id=profile_id,
        )
        path = self._app._bootstrap.config_path
        ConfigDocumentAdapter(path, safe_mode=self.store.state.safe_mode).save(document)
        self.store.dispatch(ConfigAction(document, setup_complete=True))

    async def _run_probe(self) -> None:
        if self._profile_id is None:
            self._notice("Create a profile first.")
            return
        result = await self.kernel.providers.probe(self._profile_id)
        if result.ok:
            self._notice("Probe succeeded.")
        else:
            # Informational: unreachable or no probe adapter. Explicit continue.
            self._notice(f"Probe unavailable: {result.error.message} — you may continue.")

    async def _finish(self) -> None:
        self._step_index = len(self.STEPS) - 1
        if not self.store.state.safe_mode:
            mode = AuthorizationMode.MANUAL  # default; RadioSet value when wired
            prefs = await self.kernel.preferences.snapshot()
            await self.kernel.preferences.patch(PreferencesPatch(prefs.revision, authorization_mode=mode))
        self.store.dispatch(PageAction(PageId.CHAT))
