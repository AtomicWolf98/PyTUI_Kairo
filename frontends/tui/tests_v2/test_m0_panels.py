"""M0 acceptance: context and workspace sidebars."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.enums import AuthorizationMode, LifecycleState
from kairo_kernel.contracts.identifiers import KernelId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.errors import KernelResult

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.panels.context import ContextPanel
from kairo_tui_v2.panels.workspace import WorkspacePanel
from kairo_tui_v2.reducer import WorkspaceUpdated
from kairo_tui_v2.state import WorkspaceView
from tests_v2.support.fakes import FakeEvents

SESSION = SessionId("session-1")
PROFILE = ProviderProfile(
    ProfileId("openai:gpt"),
    "OpenAI",
    "openai_responses",
    "gpt-4o",
    "https://api.openai.com/v1",
    128_000,
    16_384,
    0.7,
)


class WorkspaceKernel:
    """Fake with workspace surface and session basics."""

    def __init__(self) -> None:
        self.events = FakeEvents()
        self._changed = ("README.md", "src/main.py")
        self.workspace = WorkspaceKernel._Workspace(self)
        self.sessions = WorkspaceKernel._Sessions()
        self.conversations = WorkspaceKernel._Conversations()

    class _Workspace:
        def __init__(self, owner: WorkspaceKernel) -> None:
            self._owner = owner

        async def changed_files(self) -> KernelResult[object]:
            from kairo_kernel.services.workspaces import ChangedFiles

            return KernelResult.success(ChangedFiles("/workspace", 7, True, tuple(self._owner._changed)))

    class _Sessions:
        async def list(self) -> KernelResult[tuple]:
            return KernelResult.success(())

        async def create(self, name: str):
            from kairo_kernel.contracts.support import SessionSummary

            return KernelResult.success(
                SessionSummary(SESSION, name, 0, datetime.now(timezone.utc), datetime.now(timezone.utc))
            )

    class _Conversations:
        async def history(self, session_id: object) -> KernelResult[tuple]:
            return KernelResult.success(())

    async def status(self) -> KernelStatus:
        return KernelStatus(
            KernelId("k"),
            LifecycleState.RUNNING,
            "0.4.0a2",
            datetime.now(timezone.utc),
            "/workspace",
            7,
            PROFILE.profile_id,
            SESSION,
            TurnId("turn-1"),
            AuthorizationMode.MANUAL,
            False,
            False,
            ContextStats(1200, 128_000, 0.9),
        )

    async def active_turns(self) -> tuple:
        return ()


async def test_sidebar_hidden_by_default() -> None:
    app = KairoTuiApp(kernel=WorkspaceKernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        context = app.query_one("#context-panel", ContextPanel)
        workspace = app.query_one("#workspace-panel", WorkspacePanel)
        assert not context.has_class("sidebar-open")
        assert not workspace.has_class("sidebar-open")


async def test_toggle_cycles_context_workspace_closed() -> None:
    app = KairoTuiApp(kernel=WorkspaceKernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        context = app.query_one("#context-panel", ContextPanel)
        workspace = app.query_one("#workspace-panel", WorkspacePanel)
        app.action_toggle_sidebar()
        await pilot.pause()
        assert context.has_class("sidebar-open")
        app.action_toggle_sidebar()
        await pilot.pause()
        await pilot.pause()
        assert workspace.has_class("sidebar-open")
        app.action_toggle_sidebar()
        await pilot.pause()
        assert not context.has_class("sidebar-open")
        assert not workspace.has_class("sidebar-open")


async def test_context_panel_renders_state() -> None:
    app = KairoTuiApp(kernel=WorkspaceKernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(WorkspaceUpdated(WorkspaceView("/workspace", 7)))
        app.action_toggle_sidebar()
        await pilot.pause()
        content = str(app.query_one("#context-panel", ContextPanel).content)
        assert "Workspace revision: 7" in content
        assert "Model:" in content


async def test_workspace_panel_lists_changed_files() -> None:
    app = KairoTuiApp(kernel=WorkspaceKernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.dispatch_action(WorkspaceUpdated(WorkspaceView("/workspace", 7)))
        app.action_toggle_sidebar()  # context
        await pilot.pause()
        app.action_toggle_sidebar()  # workspace
        await pilot.pause()
        await pilot.pause()
        content = str(app.query_one("#workspace-panel", WorkspacePanel).content)
        assert "README.md" in content
        assert "src/main.py" in content
        assert "Revision: 7" in content


async def test_stale_workspace_revision_never_renders() -> None:
    app = KairoTuiApp(kernel=WorkspaceKernel())  # type: ignore[arg-type]
    async with app.run_test():
        app.dispatch_action(WorkspaceUpdated(WorkspaceView("/workspace", 9)))
        app.dispatch_action(WorkspaceUpdated(WorkspaceView("/workspace", 5)))  # stale, dropped
        assert app.state.workspace == WorkspaceView("/workspace", 9)
