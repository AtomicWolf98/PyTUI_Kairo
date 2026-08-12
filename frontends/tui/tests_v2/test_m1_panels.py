"""M1 acceptance: settings, memory, extensions and doctor panels."""

from __future__ import annotations

from datetime import datetime, timezone

from kairo_kernel.contracts.enums import AuthorizationMode, LifecycleState
from kairo_kernel.contracts.identifiers import KernelId, ProfileId, SessionId
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from kairo_kernel.errors import KernelResult

from kairo_tui_v2.app import KairoTuiApp
from kairo_tui_v2.panels.diagnostics import DiagnosticsPanel
from kairo_tui_v2.panels.extensions import ExtensionsPanel
from kairo_tui_v2.panels.memory import MemoryPanel
from kairo_tui_v2.panels.settings import SettingsPanel
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


class M1Kernel:
    """Fake with memory, skills, mcp and diagnostics surfaces."""

    def __init__(self) -> None:
        self.events = FakeEvents()
        self.memory = M1Kernel._Memory()
        self.skills = M1Kernel._Skills()
        self.mcp = M1Kernel._Mcp()
        self.diagnostics = M1Kernel._Diagnostics()
        self.providers = M1Kernel._Providers()
        self.sessions = M1Kernel._Sessions()
        self.conversations = M1Kernel._Conversations()
        self.workspace = M1Kernel._Workspace()

    class _Workspace:
        async def changed_files(self) -> KernelResult[object]:
            from kairo_kernel.services.workspaces import ChangedFiles

            return KernelResult.success(ChangedFiles("/", 0, True))

    class _Memory:
        async def search(self, query: MemoryQuery) -> KernelResult[tuple[MemoryEntry, ...]]:
            entry = MemoryEntry(
                __import__("kairo_kernel.contracts.identifiers", fromlist=["MemoryId"]).MemoryId("m1"),
                "notes",
                "key1",
                (),
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            )
            return KernelResult.success((entry,))

    class _Skills:
        async def inspect(self) -> object:
            from kairo_kernel.skills import SkillInventory

            return SkillInventory("digest", "ok", ())

    class _Mcp:
        def catalog(self) -> tuple:
            return ()

    class _Diagnostics:
        async def local(self) -> KernelResult[object]:
            from kairo_kernel.services.diagnostics import DiagnosticCheck, DiagnosticReport

            return KernelResult.success(
                DiagnosticReport(
                    "local",
                    (
                        DiagnosticCheck("kernel", "core", "ok", "Kernel is running", 12.0),
                        DiagnosticCheck("secret", "core", "failed", "secret sk-abc123 marker", 3.0),
                    ),
                    15.0,
                )
            )

    class _Providers:
        async def snapshot(self):
            from kairo_kernel.services.providers import ProviderCatalogSnapshot

            return ProviderCatalogSnapshot(0, (PROFILE,))

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


async def test_settings_panel_lists_profiles() -> None:
    app = KairoTuiApp(kernel=M1Kernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        app.action_toggle_sidebar()  # context
        await pilot.pause()
        app.action_toggle_sidebar()  # workspace
        await pilot.pause()
        app.action_toggle_sidebar()  # settings
        await pilot.pause()
        await pilot.pause()
        content = str(app.query_one("#settings-panel", SettingsPanel).content)
        assert "OpenAI" in content
        assert "gpt-4o" in content


async def test_memory_panel_lists_namespaced_entries() -> None:
    app = KairoTuiApp(kernel=M1Kernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        for _ in range(3):
            app.action_toggle_sidebar()
            await pilot.pause()
        app.action_toggle_sidebar()  # memory
        await pilot.pause()
        await pilot.pause()
        content = str(app.query_one("#memory-panel", MemoryPanel).content)
        assert "notes/key1" in content


async def test_extensions_panel_renders_empty_inventory() -> None:
    app = KairoTuiApp(kernel=M1Kernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        for _ in range(4):
            app.action_toggle_sidebar()
            await pilot.pause()
        app.action_toggle_sidebar()  # extensions
        await pilot.pause()
        await pilot.pause()
        content = str(app.query_one("#extensions-panel", ExtensionsPanel).content)
        assert "Skills" in content
        assert "No skills loaded." in content


async def test_doctor_panel_redacts_secret_markers() -> None:
    app = KairoTuiApp(kernel=M1Kernel())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        for _ in range(5):
            app.action_toggle_sidebar()
            await pilot.pause()
        app.action_toggle_sidebar()  # doctor
        await pilot.pause()
        await pilot.pause()
        content = str(app.query_one("#diagnostics-panel", DiagnosticsPanel).content)
        assert "kernel: ok" in content
        assert "sk-abc123" not in content
        assert "[redacted]" in content


async def test_diagnostics_redaction_is_pure() -> None:
    from kairo_tui_v2.panels.diagnostics import _redact

    assert _redact("boom sk-abc123") == "[redacted]"
    assert _redact("all good") == "all good"
