"""TUI command registry, slash routing, Esc priority.

Deviation from the brief (ratified in Task 8): each Pilot test bootstraps the
app synchronously (outside any event loop) and drives the Pilot inside
``asyncio.run`` — pytest-asyncio's auto-mode loop rejects the nested
``asyncio.run`` inside ``build_running_kernel``.

Task 8 additions: merged palette registry unit tests and the business-command
pilots (``/new``, ``/clear``, ``/workspace``, ``/memory`` via the composer).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel.contracts.commands import CommandOutcome, KernelCommand, ParsedCommand
from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.enums import ProviderStreamKind
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.errors import KernelResult

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, BootstrapResult, build_running_kernel
from kairo_tui.commands import TUI_COMMANDS, active_session_contract, build_command_palette, parse_tui_command
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import AppState, PageId, SessionAction
from kairo_tui.widgets import Composer
from tests.support.fakes import NOW_PROFILE, FakeProvider


def test_parse_tui_command_recognizes_nav() -> None:
    parsed = parse_tui_command("/workspace")
    assert parsed == ParsedCommand("/workspace")


def test_parse_tui_command_rejects_unknown() -> None:
    assert parse_tui_command("/does-not-exist") is None
    assert parse_tui_command("plain text") is None


def test_parse_tui_command_arg_aware_workspace() -> None:
    """/workspace with a path falls through to the kernel business command."""
    assert parse_tui_command("/workspace some/path") is None
    assert parse_tui_command("/workspace") == ParsedCommand("/workspace")


def test_parse_tui_command_arg_aware_memory() -> None:
    """/memory with a namespace falls through to the kernel business command."""
    assert parse_tui_command("/memory ns text") is None
    assert parse_tui_command("/memory") == ParsedCommand("/memory")


def test_parse_tui_command_arg_aware_doctor() -> None:
    """/doctor with arguments falls through to the kernel diagnostics command."""
    assert parse_tui_command("/doctor full") is None
    assert parse_tui_command("/doctor") == ParsedCommand("/doctor")


def test_nav_command_switches_page(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            from kairo_tui.commands import execute_tui_command

            parsed = parse_tui_command("/settings")
            assert parsed is not None
            handled = await execute_tui_command(app, parsed)
            assert handled is True
            assert app.store.state.page is PageId.SETTINGS

    asyncio.run(drive())


def test_esc_cancels_foreground_turn(workspace) -> None:
    from kairo_kernel.contracts.turns import TurnRequest

    from tests.support.fakes import FakeProvider

    provider = FakeProvider(block=True)
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
        provider=provider,
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Chat")
            assert created.value is not None
            await app.kernel.submit(TurnRequest("block me", session_id=created.value.session_id))
            app.store.dispatch(SessionAction(str(created.value.session_id)))
            await pilot.pause()
            assert app.store.state.active_turns != ()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.pause()
            assert app.store.state.active_turns == ()

    asyncio.run(drive())


def test_esc_is_noop_without_modal_or_turn(workspace) -> None:
    bootstrap = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
    )
    app = KairoTuiApp(bootstrap)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            # Textual always keeps the base screen on the stack; Esc with no
            # pushed modal and no active turn must be a no-op (stack unchanged).
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


# --- Task 8: unified command registry --------------------------------------


class _PaletteStubKernel:
    """Minimal kernel surface: only the command catalog the palette reads."""

    class _Commands:
        def catalog(self) -> tuple[KernelCommand, ...]:
            return (
                KernelCommand("/status", "Show status", "Show read-only kernel status"),
                KernelCommand("/sessions", "List sessions", "List persisted sessions"),
                KernelCommand("/new", "Create a session", "Create a new persisted session"),
                KernelCommand("/workspace", "Switch workspace", "Move the workspace to a path or bookmark"),
            )

    commands = _Commands()


class _PaletteStubApp:
    kernel = _PaletteStubKernel()


def test_build_command_palette_merges_tui_and_kernel_with_tui_precedence() -> None:
    """The palette registry merges TUI + kernel commands; TUI wins on name clash."""
    entries = build_command_palette(_PaletteStubApp())
    names = [entry.name for entry in entries]
    by_name = {entry.name: entry for entry in entries}
    # TUI commands lead in registry order, then the kernel-only commands.
    assert names[: len(TUI_COMMANDS)] == list(TUI_COMMANDS)
    # Kernel-only commands carry the kernel flag and their catalog text.
    assert "/status" in by_name
    assert by_name["/status"].kernel is True
    assert by_name["/status"].summary == "Show status"
    assert by_name["/status"].help == "Show read-only kernel status"
    assert by_name["/new"].kernel is True
    # Name clashes resolve to the TUI nav command (the kernel variant is dropped).
    assert by_name["/sessions"].kernel is False
    assert by_name["/sessions"].summary == TUI_COMMANDS["/sessions"]
    assert by_name["/workspace"].kernel is False
    assert by_name["/workspace"].summary == TUI_COMMANDS["/workspace"]


def test_active_session_contract() -> None:
    """The store's active session maps to a kernel SessionId; None when inactive."""
    assert active_session_contract(AppState(active_session_id="session-1")) == SessionId("session-1")
    assert active_session_contract(AppState()) is None


def _seeded_bootstrap(workspace: Path, *, provider=None) -> BootstrapResult:
    """A booted app dependency set with setup complete (composer enabled, Chat page)."""
    document = ConfigDocument(
        profiles=(NOW_PROFILE,),
        roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
        default_profile_id=NOW_PROFILE.profile_id,
    )
    ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
    return build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
        secret_store=SecretStore(None),
        provider=provider,
    )


async def _submit_via_composer(pilot, app: KairoTuiApp, text: str) -> None:
    """Type ``text`` in the composer and press Enter (slash commands included)."""
    await pilot.click("#composer")
    composer = app.query_one("#composer", Composer)
    composer.focus()
    composer.text = text
    await pilot.press("enter")


def _content(text: str) -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.CONTENT, content=(TextBlock(text),))


def _completed() -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED)


async def _wait_for(pilot, predicate, *, polls: int = 40, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


def test_new_command_via_composer_creates_session_on_chat(workspace) -> None:
    """/new via the composer runs the kernel command and stays on the Chat page."""
    app = KairoTuiApp(_seeded_bootstrap(workspace))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.store.state.page is PageId.CHAT  # setup-complete boot page
            before = (await app.kernel.sessions.list()).value or ()
            await _submit_via_composer(pilot, app, "/new")
            await pilot.pause()
            after = (await app.kernel.sessions.list()).value or ()
            assert len(after) == len(before) + 1
            assert app.store.state.page is PageId.CHAT

    asyncio.run(drive())


def test_clear_command_clears_active_session_history(workspace) -> None:
    """/clear with an active session empties its history (session id reaches the kernel)."""
    provider = FakeProvider((_content("Hello "), _completed()))
    app = KairoTuiApp(_seeded_bootstrap(workspace, provider=provider))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Chat")
            assert created.ok and created.value is not None
            session_id = created.value.session_id
            app.store.dispatch(SessionAction(str(session_id)))
            accepted = await app.kernel.submit(TurnRequest("hi", session_id=session_id))
            assert accepted.ok
            await _wait_for(pilot, lambda: not app.store.state.active_turns)
            await pilot.pause()
            history = (await app.kernel.conversations.history(session_id)).value or ()
            assert history != ()  # the turn committed messages first
            await _submit_via_composer(pilot, app, "/clear")
            await pilot.pause()
            await pilot.pause()
            cleared = (await app.kernel.conversations.history(session_id)).value or ()
            assert cleared == ()

    asyncio.run(drive())


def test_workspace_command_moves_workspace_not_nav(workspace) -> None:
    """/workspace <path> is the kernel command (moves the root), never the TUI nav."""
    target = workspace.parent / "elsewhere"
    target.mkdir()
    app = KairoTuiApp(_seeded_bootstrap(workspace))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, f"/workspace {target}")
            await pilot.pause()
            await pilot.pause()
            snapshot = await app.kernel.workspace.snapshot()
            assert Path(snapshot.root) == target.resolve()
            assert app.store.state.page is PageId.CHAT  # no nav to the Workspace page

    asyncio.run(drive())


def test_memory_command_runs_kernel_search_and_reports(workspace, monkeypatch) -> None:
    """/memory ns text falls through to the kernel search command, not nav."""
    app = KairoTuiApp(_seeded_bootstrap(workspace))
    calls: list[tuple[ParsedCommand, SessionId | None]] = []
    results: list[KernelResult[CommandOutcome]] = []
    original = app.kernel.commands.execute

    async def spy(parsed: ParsedCommand, session_id: SessionId | None = None) -> KernelResult[CommandOutcome]:
        calls.append((parsed, session_id))
        result = await original(parsed, session_id)
        results.append(result)
        return result

    monkeypatch.setattr(app.kernel.commands, "execute", spy)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "/memory ns text")
            await pilot.pause()
            await pilot.pause()
            # The kernel search command ran (with no active session → None) and
            # reported a successful outcome.
            assert calls == [(ParsedCommand("/memory", ("ns", "text")), None)]
            assert results[-1].ok
            assert app.store.state.page is PageId.CHAT  # never treated as nav

    asyncio.run(drive())
