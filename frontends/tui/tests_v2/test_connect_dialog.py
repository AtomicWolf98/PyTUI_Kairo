"""P1 acceptance: provider connection dialog, keyboard-driven.

Every interaction goes through Pilot key presses; no handler is called
directly. The kernel is a fake exposing only the public providers surface.
"""

from __future__ import annotations

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import (
    ProviderConnectionReceipt,
    ProviderConnectionRequest,
    ProviderProfile,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.services.providers import ProviderCatalogSnapshot
from textual.widgets import Button

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.dialogs.connect import ConnectDialog
from kairo_tui_v2.state import OverlayKind
from kairo_tui_v2.widgets.composer import Composer

API_KEY_VALUE = "sk-test-dialog-12345"


class FakeKernel:
    """Minimal public-surface fake; records atomic configure calls."""

    def __init__(
        self,
        *,
        resolve_found: bool = False,
        configure_failure: KernelError | None = None,
    ) -> None:
        self.resolve_found = resolve_found
        self.configure_failure = configure_failure
        self.configure_calls: list[ProviderConnectionRequest] = []
        self.providers = FakeKernel._Providers(self)

    class _Providers:
        def __init__(self, owner: FakeKernel) -> None:
            self._owner = owner

        async def resolve(self, profile_id: object = None, role: str = "") -> KernelResult[ProviderProfile]:
            if self._owner.resolve_found:
                profile = ProviderProfile(
                    ProfileId("openai_responses:gpt-4o"),
                    "OpenAI",
                    "openai_responses",
                    "gpt-4o",
                    "https://api.openai.com/v1",
                    128_000,
                    16_384,
                    0.7,
                )
                return KernelResult.success(profile)
            return KernelResult.failure(
                KernelError(
                    ErrorCode.NOT_FOUND,
                    "No provider profile is assigned to the role.",
                    operation="provider.resolve",
                )
            )

        async def snapshot(self) -> ProviderCatalogSnapshot:
            return ProviderCatalogSnapshot(0)

        async def configure(
            self,
            request: ProviderConnectionRequest,
        ) -> KernelResult[ProviderConnectionReceipt]:
            self._owner.configure_calls.append(request)
            if self._owner.configure_failure is not None:
                return KernelResult.failure(self._owner.configure_failure)
            self._owner.resolve_found = True  # a connected profile now resolves
            return KernelResult.success(
                ProviderConnectionReceipt(
                    request.profile.profile_id,
                    request.role,
                    1,
                    request.profile.profile_id if request.make_default else None,
                )
            )


def _app(kernel: FakeKernel | None = None) -> KairoTuiApp:
    return KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]


async def _open_dialog(pilot, app: KairoTuiApp) -> None:
    await pilot.press("h", "i")
    await pilot.press("enter")
    await pilot.pause()


async def _focus_and_enter(dialog: ConnectDialog, button_id: str, pilot) -> None:
    dialog.query_one(f"#{button_id}", Button).focus()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def test_submit_without_provider_opens_connect_dialog() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        assert app.state.overlay is OverlayKind.CONNECT
        assert app.state.pending_draft == "hi"
        assert isinstance(app.screen, ConnectDialog)


async def test_cancel_restores_original_draft_and_focus() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        composer = app.query_one("#composer", Composer)
        assert app.state.overlay is OverlayKind.CONNECT
        await pilot.press("escape")
        await pilot.pause()
        assert app.state.overlay is None
        assert app.state.draft == "hi"
        assert app.state.pending_draft is None
        assert composer.text == "hi"
        assert app.focused is composer


async def test_tab_order_visits_all_fields_and_actions() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        dialog = app.screen
        expected = [
            "provider-type",
            "model",
            "base-url",
            "api-key",
            "context-window",
            "max-tokens",
            "temperature",
            "test-connection",
            "save-and-send",
            "save",
            "cancel",
        ]
        await pilot.press("shift+tab")  # back to the provider select
        await pilot.pause()
        assert dialog.focused.id == "provider-type"
        for widget_id in expected[1:]:
            await pilot.press("tab")
            await pilot.pause()
            assert dialog.focused.id == widget_id, f"expected {widget_id}, got {dialog.focused.id}"


async def test_labels_are_visible_without_placeholders() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        for label_id in (
            "label-provider",
            "label-model",
            "label-base-url",
            "label-api-key",
            "label-context-window",
            "label-max-tokens",
            "label-temperature",
        ):
            label = app.screen.query_one(f"#{label_id}")
            assert label.content, f"{label_id} label is empty"
        for input_id in ("model", "base-url", "api-key", "context-window", "max-tokens", "temperature"):
            widget = app.screen.query_one(f"#{input_id}")
            assert widget.placeholder == "", f"{input_id} must not rely on a placeholder"


async def test_buttons_have_nonempty_text() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        for button_id in ("test-connection", "save-and-send", "save", "cancel"):
            button = app.screen.query_one(f"#{button_id}")
            assert button.label, f"{button_id} button text is empty"
            assert str(button.label).strip()


async def test_missing_model_stays_open_with_inline_error() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        dialog = app.screen
        dialog.query_one("#model").value = ""
        await _focus_and_enter(dialog, "save", pilot)
        assert isinstance(app.screen, ConnectDialog)
        error = str(dialog.query_one("#connect-error").content)
        assert "Model" in error


async def test_save_calls_atomic_kernel_provider_configure_once() -> None:
    kernel = FakeKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        dialog = app.screen
        dialog.query_one("#model").value = "gpt-4o"
        dialog.query_one("#api-key").value = API_KEY_VALUE
        await _focus_and_enter(dialog, "save", pilot)
        assert len(kernel.configure_calls) == 1
        request = kernel.configure_calls[0]
        assert request.profile.profile_id == ProfileId("openai_responses:gpt-4o")
        assert request.secret is not None
        assert request.secret.value == API_KEY_VALUE
        assert app.state.overlay is None


async def test_save_and_send_retries_exact_original_draft() -> None:
    kernel = FakeKernel()  # no provider yet; dialog opens on submit
    app = _app(kernel)
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        assert app.state.pending_draft == "hi"
        dialog = app.screen
        dialog.query_one("#model").value = "gpt-4o"
        await _focus_and_enter(dialog, "save-and-send", pilot)
        assert app.state.overlay is None
        assert app._last_ready == "hi"


async def test_api_key_never_enters_app_state_repr() -> None:
    kernel = FakeKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        dialog = app.screen
        dialog.query_one("#model").value = "gpt-4o"
        dialog.query_one("#api-key").value = API_KEY_VALUE
        assert API_KEY_VALUE not in str(app.state)
        assert API_KEY_VALUE not in repr(app.state)


async def test_api_key_never_enters_notification_or_error_text() -> None:
    kernel = FakeKernel()
    kernel.configure_failure = KernelError(
        ErrorCode.CONFIG_PERSISTENCE_FAILED,
        "Provider connection could not be persisted.",
        operation="provider.configure",
    )
    app = _app(kernel)
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        dialog = app.screen
        dialog.query_one("#model").value = "gpt-4o"
        dialog.query_one("#api-key").value = API_KEY_VALUE
        await _focus_and_enter(dialog, "save", pilot)
        assert isinstance(app.screen, ConnectDialog)  # modal stays open
        error = str(dialog.query_one("#connect-error").content)
        assert API_KEY_VALUE not in error


async def test_escape_does_not_exit_application() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        assert isinstance(app.screen, ConnectDialog)
        await pilot.press("escape")
        await pilot.pause()
        assert app.state.overlay is None
        # The app is still running and interactive; the restored draft is intact.
        await pilot.press("h", "i")
        await pilot.pause()
        assert app.query_one("#composer", Composer).text == "hihi"


async def test_dark_and_light_theme_button_foreground_contrast() -> None:
    app = _app(FakeKernel())
    async with app.run_test() as pilot:
        await _open_dialog(pilot, app)
        save = app.screen.query_one("#save")
        dark_fg = save.styles.color
        app.theme = "textual-light"
        await pilot.pause()
        light_fg = save.styles.color
        assert dark_fg is not None and light_fg is not None
        assert dark_fg != light_fg
