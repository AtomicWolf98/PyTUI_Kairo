"""D0 acceptance: command palette, session picker, model picker."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.commands import CommandOutcome, KernelCommand, ParsedCommand
from kairo_kernel.contracts.enums import AuthorizationMode, LifecycleState
from kairo_kernel.contracts.identifiers import KernelId, ProfileId, SessionId
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SessionSummary
from kairo_kernel.errors import KernelResult
from kairo_kernel.services.providers import ProviderCatalogSnapshot

from kairo_tui.app import KairoTuiApp
from kairo_tui.dialogs.commands import CommandPalette
from kairo_tui.dialogs.models import ModelPicker
from kairo_tui.dialogs.sessions import SessionPicker
from kairo_tui.widgets.composer import Composer

SESSION = SessionId("session-1")
PROFILE = ProviderProfile(
    ProfileId("openai_responses:gpt-4o"),
    "OpenAI",
    "openai_responses",
    "gpt-4o",
    "https://api.openai.com/v1",
    128_000,
    16_384,
    0.7,
)


class PickerKernel:
    """Fake with session, provider, command and preference surfaces."""

    def __init__(self) -> None:
        self.sessions = PickerKernel._Sessions(self)
        self.providers = PickerKernel._Providers(self)
        self.conversations = PickerKernel._Conversations(self)
        self.commands = PickerKernel._Commands(self)
        self.preferences = PickerKernel._Preferences(self)
        from support.fakes import FakeEvents

        self.events = FakeEvents()
        self.renamed: list[tuple[object, str]] = []
        self.deleted: list[object] = []
        self.executed: list[str] = []
        self.selected_profile: list[object] = []
        self._sessions = [
            SessionSummary(SESSION, "Notes", 3, _now(), _now()),
            SessionSummary(SessionId("session-2"), "Refactor", 1, _now(), _now()),
        ]
        self._profiles = (PROFILE,)

    class _Sessions:
        def __init__(self, owner: PickerKernel) -> None:
            self._owner = owner

        async def list(self) -> KernelResult[tuple[SessionSummary, ...]]:
            return KernelResult.success(tuple(self._owner._sessions))

        async def create(self, name: str) -> KernelResult[SessionSummary]:
            session = SessionSummary(SessionId(f"session-{len(self._owner._sessions) + 1}"), name, 0, _now(), _now())
            self._owner._sessions.append(session)
            return KernelResult.success(session)

        async def rename(self, session_id: object, name: str) -> KernelResult[SessionSummary]:
            self._owner.renamed.append((session_id, name))
            return KernelResult.success(self._owner._sessions[0])

        async def delete(self, session_id: object) -> KernelResult[bool]:
            self._owner.deleted.append(session_id)
            self._owner._sessions = [item for item in self._owner._sessions if item.session_id != session_id]
            return KernelResult.success(True)

    class _Conversations:
        def __init__(self, owner: PickerKernel) -> None:
            self._owner = owner

        async def history(self, session_id: object) -> KernelResult[tuple]:
            return KernelResult.success(())

        async def compress(self, session_id: object, instructions: str = "") -> KernelResult[object]:
            return KernelResult.success(None)

    class _Providers:
        def __init__(self, owner: PickerKernel) -> None:
            self._owner = owner

        async def resolve(self, profile_id: object = None, role: str = "") -> KernelResult[ProviderProfile]:
            return KernelResult.success(PROFILE)

        async def snapshot(self) -> ProviderCatalogSnapshot:
            return ProviderCatalogSnapshot(0, self._owner._profiles)

    class _Commands:
        def __init__(self, owner: PickerKernel) -> None:
            self._owner = owner

        def catalog(self) -> tuple[KernelCommand, ...]:
            return (KernelCommand("workspace", "Show workspace state", ""),)

        def parse(self, text: str) -> KernelResult[ParsedCommand]:
            return KernelResult.success(ParsedCommand(text))

        async def execute(self, parsed: ParsedCommand, session_id: object = None) -> KernelResult[CommandOutcome]:
            self._owner.executed.append(parsed.name)
            return KernelResult.success(CommandOutcome(parsed.name, "", True))

    class _Preferences:
        def __init__(self, owner: PickerKernel) -> None:
            self._owner = owner

        async def patch(self, patch: object) -> KernelResult[object]:
            self._owner.selected_profile.append(getattr(patch, "profile_id", None))
            return KernelResult.success("ok")

    async def status(self) -> KernelStatus:
        return KernelStatus(
            KernelId("k"),
            LifecycleState.RUNNING,
            "0.4.0a2",
            _now(),
            "/",
            0,
            None,
            None,
            None,
            AuthorizationMode.MANUAL,
            False,
            False,
            ContextStats(0, 0, 0.0),
        )

    async def active_turns(self) -> tuple:
        return ()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _app(kernel: PickerKernel) -> KairoTuiApp:
    return KairoTuiApp(kernel=kernel)  # type: ignore[arg-type]


async def test_ctrl_p_opens_palette_with_focus() -> None:
    app = _app(PickerKernel())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        assert app.screen.focused is app.screen.query_one("#command-search")


async def test_palette_filters_case_insensitive_and_shows_empty() -> None:
    app = _app(PickerKernel())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.pause()
        palette = app.screen
        palette.query_one("#command-search").value = "zzz-no-match"
        await pilot.pause()
        assert palette.query_one("#command-empty").display
        palette.query_one("#command-search").value = "SESSION"
        await pilot.pause()
        assert not palette.query_one("#command-empty").display


async def test_palette_escape_closes_without_exit() -> None:
    app = _app(PickerKernel())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        await pilot.press("h", "i")
        await pilot.pause()
        assert app.query_one("#composer", Composer).text == "hi"


async def test_palette_kernel_command_executes() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.pause()
        palette = app.screen
        palette.query_one("#command-search").value = "workspace"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert kernel.executed == ["/workspace"]


async def test_session_picker_lists_and_switches() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        app.action_session_picker()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        list_view = app.screen.query_one("#session-list")
        assert len(list_view.children) == 2
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.state.active_session_id == SESSION


async def test_session_new_creates_and_activates() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        app.action_new_session()
        await pilot.pause()
        await pilot.pause()
        assert len(kernel._sessions) == 3
        assert app.state.active_session_id == kernel._sessions[-1].session_id


async def test_session_rename_and_delete_route_to_kernel() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        app.action_session_picker()
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        screen.query_one("#session-rename").press()
        await pilot.pause()
        assert kernel.renamed == [(SESSION, "Notes-renamed")]
        screen.query_one("#session-delete").press()
        await pilot.pause()
        assert kernel.deleted == [SESSION]


async def test_model_picker_lists_profiles_and_selects() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        app.action_model_picker()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ModelPicker)
        list_view = app.screen.query_one("#model-list")
        assert len(list_view.children) == 1
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert kernel.selected_profile == [ProfileId("openai_responses:gpt-4o")]


async def test_leader_sequence_new_session() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+x", "n")
        await pilot.pause()
        await pilot.pause()
        assert len(kernel._sessions) == 3


async def test_model_picker_connect_opens_connect_dialog() -> None:
    kernel = PickerKernel()
    app = _app(kernel)
    async with app.run_test() as pilot:
        app.action_model_picker()
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#model-connect").press()
        await pilot.pause()
        await pilot.pause()
        from kairo_tui.dialogs.connect import ConnectDialog
        from kairo_tui.state import OverlayKind

        assert app.state.overlay is OverlayKind.CONNECT
        assert isinstance(app.screen, ConnectDialog)
