"""Provider connection modal with a frozen field order and focus contract."""

from __future__ import annotations

from dataclasses import replace

from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.providers import (
    ProviderConnectionRequest,
    ProviderProfile,
)
from kairo_kernel.contracts.support import SecretInput
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("OpenAI Responses", "openai_responses"),
    ("OpenAI Chat Completions", "openai_chat"),
    ("Anthropic", "anthropic"),
)

DEFAULT_BASE_URLS: dict[str, str] = {
    "openai_responses": "https://api.openai.com/v1",
    "openai_chat": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


class ConnectDialog(ModalScreen[None]):
    """Fixed field order: provider, model, base URL, API key, limits, actions."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    class SaveRequested(Message):
        """One atomic connection request; the app supplies the revision."""

        def __init__(self, request: ProviderConnectionRequest, *, send_after: bool) -> None:
            super().__init__()
            self.request = request
            self.send_after = send_after

    class Canceled(Message):
        """Escape or the Cancel button; restore the pending draft."""

    def compose(self) -> ComposeResult:
        with Vertical(id="connect-modal"):
            yield Static("Connect a model", id="connect-title")
            yield Label("Provider type", id="label-provider")
            yield Select(
                PROVIDER_OPTIONS,
                value="openai_responses",
                id="provider-type",
            )
            yield Label("Model", id="label-model")
            yield Input(id="model", placeholder="")
            yield Label("Base URL", id="label-base-url")
            yield Input(id="base-url", placeholder="")
            yield Label("API key", id="label-api-key")
            yield Input(id="api-key", password=True, placeholder="")
            yield Label("Context window", id="label-context-window")
            yield Input(id="context-window", value="128000", placeholder="")
            yield Label("Max output tokens", id="label-max-tokens")
            yield Input(id="max-tokens", value="16384", placeholder="")
            yield Label("Temperature", id="label-temperature")
            yield Input(id="temperature", value="0.7", placeholder="")
            yield Static("", id="connect-error")
            with Horizontal(id="connect-actions"):
                yield Button("Test connection", id="test-connection", variant="primary")
                yield Button("Save and send", id="save-and-send")
                yield Button("Save", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#model", Input).focus()

    @on(Select.Changed)
    def on_provider_changed(self, message: Select.Changed) -> None:
        """Refresh the Base URL default when the provider kind changes."""
        base_url = self.query_one("#base-url", Input)
        if isinstance(message.value, str):
            base_url.value = DEFAULT_BASE_URLS.get(message.value, "")

    def action_cancel(self) -> None:
        self.post_message(self.Canceled())

    @on(Button.Pressed, "#cancel")
    def on_cancel_pressed(self, message: Button.Pressed) -> None:
        self.post_message(self.Canceled())

    @on(Button.Pressed, "#save")
    def on_save_pressed(self, message: Button.Pressed) -> None:
        self._request_save(send_after=False)

    @on(Button.Pressed, "#save-and-send")
    def on_save_and_send_pressed(self, message: Button.Pressed) -> None:
        self._request_save(send_after=True)

    @on(Button.Pressed, "#test-connection")
    def on_test_connection_pressed(self, message: Button.Pressed) -> None:
        # P1: connection probing arrives with the full turn pipeline (C1).
        self.show_error("Connection test is not available yet.")

    def _request_save(self, *, send_after: bool) -> None:
        request, error = self._build_request()
        if request is None:
            self.show_error(error)
            self.query_one("#model", Input).focus()
            return
        self.post_message(self.SaveRequested(request, send_after=send_after))

    def _build_request(self) -> tuple[ProviderConnectionRequest | None, str]:
        """Validate fields and build one atomic request; never a UI-side chain."""
        provider = self.query_one("#provider-type", Select).value
        model = self.query_one("#model", Input).value.strip()
        base_url = self.query_one("#base-url", Input).value.strip()
        api_key = self.query_one("#api-key", Input).value
        context_window = self.query_one("#context-window", Input).value.strip()
        max_tokens = self.query_one("#max-tokens", Input).value.strip()
        temperature = self.query_one("#temperature", Input).value.strip()
        if not isinstance(provider, str):
            return None, "Provider type is required."
        if not model:
            return None, "Model is required."
        try:
            context_window_value = int(context_window)
            max_tokens_value = int(max_tokens)
            temperature_value = float(temperature)
        except ValueError:
            return None, "Context window, max output tokens and temperature must be numbers."
        profile_id = f"{provider}:{model}"
        profile = ProviderProfile(
            ProfileId(profile_id),
            f"{provider} · {model}",
            provider,
            model,
            base_url,
            context_window_value,
            max_tokens_value,
            temperature_value,
            secret_id=profile_id if api_key else "",
        )
        secret = SecretInput(SecretId(profile_id), api_key) if api_key else None
        return (
            ProviderConnectionRequest(profile, secret=secret, role="chat", make_default=True),
            "",
        )

    def show_error(self, message: str) -> None:
        """Inline redacted error; the modal stays open and keeps focus."""
        self.query_one("#connect-error", Static).update(f"[red]{message}[/red]")
        self.query_one("#connect-error", Static).focus()

    def clear_secret_widget(self) -> None:
        """Wipe the password widget after a successful save."""
        self.query_one("#api-key", Input).value = ""

    def refresh_revision(self, request: ProviderConnectionRequest, revision: int) -> ProviderConnectionRequest:
        """Return the request with the current catalog revision attached."""
        return replace(request, expected_revision=revision)
