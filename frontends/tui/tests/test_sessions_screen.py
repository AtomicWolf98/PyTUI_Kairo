"""SessionsScreen: list/search/switch/rename/delete/export + clear/undo/compress.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_chat_screen.py,
because pytest-asyncio's auto-mode loop rejects the nested ``asyncio.run``
inside ``build_running_kernel``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import MessageKind, MessageRole, ProviderStreamKind
from kairo_kernel.contracts.identifiers import MessageId, SessionId
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.sessions import SessionTextModal
from kairo_tui.store import SessionAction, SessionsAction
from tests.support.fakes import NOW_PROFILE, FakeProvider


@pytest.fixture
def sessions_app_factory(workspace: Path):
    """A booted KairoTuiApp on the Chat page with a seeded config + fake provider."""

    def make(*, provider=None, size=(140, 40)) -> KairoTuiApp:
        document = ConfigDocument(
            profiles=(NOW_PROFILE,),
            roles=(RoleMapping("chat", NOW_PROFILE.profile_id),),
            default_profile_id=NOW_PROFILE.profile_id,
        )
        ConfigDocumentAdapter(workspace.parent / "config-v1.json").save(document)
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace), config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
            provider=provider or FakeProvider(),
        )
        return KairoTuiApp(bootstrap)
    return make


async def _wait_for(pilot, predicate, *, polls: int = 60, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


def _row_label(app: KairoTuiApp, session_id: str) -> str:
    """Plain text of a session row button; '' while the list is mid-rebuild."""
    row = app.query_one_optional(f"#ses-{session_id}", Button)
    return str(row.label) if row is not None else ""


def _row_variant(app: KairoTuiApp, session_id: str) -> str:
    """Variant of a session row button; '' while the list is mid-rebuild."""
    row = app.query_one_optional(f"#ses-{session_id}", Button)
    return row.variant if row is not None else ""


async def _open_sessions(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+2")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#sessions-screen") is not None)


async def _submit(pilot, app: KairoTuiApp, session_id: str, text: str) -> None:
    """Submit a turn directly via the kernel and wait until it finishes."""
    accepted = await app.kernel.submit(TurnRequest(text, session_id=SessionId(session_id)))
    assert accepted.ok and accepted.value is not None
    await _wait_for(pilot, lambda: not app.store.state.active_turns)


def _completed() -> ProviderStreamEvent:
    return ProviderStreamEvent(kind=ProviderStreamKind.COMPLETED)


def test_list_renders_after_mount(sessions_app_factory) -> None:
    """Mount auto-runs refresh_sessions: rows appear without seeding the store."""
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Alpha")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            assert app.query_one(f"#ses-{session_id}", Button) is not None

    asyncio.run(drive())


def test_search_filters_rows(sessions_app_factory) -> None:
    """Typing in the search Input filters the rendered rows locally."""
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            alpha = await app.kernel.sessions.create("Alpha")
            beta = await app.kernel.sessions.create("Beta")
            assert alpha.ok and alpha.value is not None
            assert beta.ok and beta.value is not None
            alpha_id = str(alpha.value.session_id)
            beta_id = str(beta.value.session_id)
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(
                pilot,
                lambda: list_widget.query(f"#ses-{alpha_id}") and list_widget.query(f"#ses-{beta_id}"),
            )
            search = app.query_one("#sessions-search", Input)
            search.focus()
            await pilot.press(*tuple("Alpha"))
            # Wait atomically: the list is rebuilt via an async worker
            # (remove_children/mount), so the filtered state can be observed
            # mid-rebuild — wait for Beta gone AND Alpha present together.
            await _wait_for(
                pilot,
                lambda: list_widget.query_one_optional(f"#ses-{beta_id}", Button) is None
                and list_widget.query_one_optional(f"#ses-{alpha_id}", Button) is not None,
            )

    asyncio.run(drive())


def test_switch_selects_without_cancelling_running_turn(sessions_app_factory) -> None:
    """Clicking a row only dispatches SessionAction: another session's running
    turn (FakeProvider block=True) stays active."""
    provider = FakeProvider(block=True)
    app = sessions_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created_a = await app.kernel.sessions.create("Blocking")
            created_b = await app.kernel.sessions.create("Quiet")
            assert created_a.ok and created_a.value is not None
            assert created_b.ok and created_b.value is not None
            session_a = str(created_a.value.session_id)
            session_b = str(created_b.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_a))
            accepted = await app.kernel.submit(TurnRequest("block me", session_id=SessionId(session_a)))
            assert accepted.ok and accepted.value is not None
            await _wait_for(pilot, lambda: app.store.state.active_turns)
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_b}"))
            await pilot.click(f"#ses-{session_b}")
            await _wait_for(pilot, lambda: app.store.state.active_session_id == session_b)
            assert len(app.store.state.active_turns) == 1
            assert str(app.store.state.active_turns[0].session_id) == session_a

    asyncio.run(drive())


def test_rename_via_modal_persists_to_kernel(sessions_app_factory) -> None:
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Old Name")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_id))
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            await pilot.click("#sessions-rename")
            await _wait_for(pilot, lambda: isinstance(app.screen, SessionTextModal))
            modal = cast(SessionTextModal, app.screen)
            modal.query_one("#session-text-input", Input).value = "New Name"
            modal.query_one("#session-text-input", Input).focus()
            await pilot.press("enter")
            await _wait_for(pilot, lambda: not isinstance(app.screen, SessionTextModal))
            listed = (await app.kernel.sessions.list()).value or ()
            names = [summary.name for summary in listed]
            assert "New Name" in names
            assert "Old Name" not in names
            # The row is rebuilt by an async worker: re-query it inside the wait.
            await _wait_for(
                pilot,
                lambda: _row_label(app, session_id) is not None and "New Name" in _row_label(app, session_id),
            )

    asyncio.run(drive())


def test_delete_removes_and_activates_survivor(sessions_app_factory) -> None:
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            doomed = await app.kernel.sessions.create("Doomed")
            survivor = await app.kernel.sessions.create("Survivor")
            assert doomed.ok and doomed.value is not None
            assert survivor.ok and survivor.value is not None
            doomed_id = str(doomed.value.session_id)
            survivor_id = str(survivor.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(doomed_id))
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{doomed_id}"))
            await pilot.click("#sessions-delete")
            await _wait_for(
                pilot,
                lambda: list_widget.query_one_optional(f"#ses-{doomed_id}", Button) is None
                and app.store.state.active_session_id == survivor_id,
            )
            remaining = (await app.kernel.sessions.list()).value or ()
            assert [str(summary.session_id) for summary in remaining] == [survivor_id]
            await _wait_for(pilot, lambda: _row_variant(app, survivor_id) == "primary")

    asyncio.run(drive())


def test_export_writes_json_file_containing_session_id(sessions_app_factory, workspace: Path) -> None:
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Export Me")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_id))
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            await pilot.click("#sessions-export")
            export_path = workspace / "kairo_exports" / f"session-{session_id}.json"
            await _wait_for(pilot, lambda: export_path.exists())
            assert session_id in export_path.read_text(encoding="utf-8")

    asyncio.run(drive())


def test_clear_empties_conversation(sessions_app_factory) -> None:
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Cleared")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            app.store.dispatch(SessionAction(session_id))
            await _submit(pilot, app, session_id, "hello there")
            history = (await app.kernel.conversations.history(SessionId(session_id))).value or ()
            assert len(history) == 2  # user + assistant
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            await pilot.click("#sessions-clear")
            for _ in range(60):
                await pilot.pause(0.05)
                history = (await app.kernel.conversations.history(SessionId(session_id))).value or ()
                if not history:
                    break
            assert history == ()

    asyncio.run(drive())


def test_undo_removes_latest_turn(sessions_app_factory) -> None:
    # One COMPLETED script per turn: each submit commits a user + assistant message.
    app = sessions_app_factory(provider=FakeProvider((_completed(),), (_completed(),)))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Undone")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            app.store.dispatch(SessionAction(session_id))
            await _submit(pilot, app, session_id, "first")
            await _submit(pilot, app, session_id, "second")
            history = (await app.kernel.conversations.history(SessionId(session_id))).value or ()
            assert len(history) == 4
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            await pilot.click("#sessions-undo")
            for _ in range(60):
                await pilot.pause(0.05)
                history = (await app.kernel.conversations.history(SessionId(session_id))).value or ()
                if len(history) == 2:
                    break
            assert len(history) == 2
            assert history
            first_texts = [block.text for block in history[0].content if isinstance(block, TextBlock)]
            assert first_texts == ["first"]

    asyncio.run(drive())


def test_compress_modal_invokes_kernel_compress(sessions_app_factory) -> None:
    """Five seeded turns → compress through the modal bumps compression_count to 1."""
    messages = tuple(
        Message(
            MessageId(f"m{index}"),
            MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            MessageKind.CHAT,
            (TextBlock(f"message {index}"),),
        )
        for index in range(10)
    )
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Compressible", messages=messages)
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(session_id))
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query(f"#ses-{session_id}"))
            await pilot.click("#sessions-compress")
            await _wait_for(pilot, lambda: isinstance(app.screen, SessionTextModal))
            modal = cast(SessionTextModal, app.screen)
            modal.query_one("#session-text-input", Input).value = "summarized older context"
            modal.query_one("#session-text-input", Input).focus()
            await pilot.press("enter")
            loaded = None
            for _ in range(60):
                await pilot.pause(0.05)
                fetched = await app.kernel.sessions.get(SessionId(session_id))
                if fetched.ok and fetched.value is not None and fetched.value.compression_count == 1:
                    loaded = fetched.value
                    break
            assert loaded is not None and loaded.compression_count == 1

    asyncio.run(drive())


def test_running_session_shows_badge_in_row(sessions_app_factory) -> None:
    """A session with an active turn carries a ● in its row label; idle rows don't."""
    provider = FakeProvider(block=True)
    app = sessions_app_factory(provider=provider)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            running = await app.kernel.sessions.create("Running")
            idle = await app.kernel.sessions.create("Idle")
            assert running.ok and running.value is not None
            assert idle.ok and idle.value is not None
            running_id = str(running.value.session_id)
            idle_id = str(idle.value.session_id)
            app.store.dispatch(SessionsAction((await app.kernel.sessions.list()).value or ()))
            app.store.dispatch(SessionAction(running_id))
            accepted = await app.kernel.submit(TurnRequest("block me", session_id=SessionId(running_id)))
            assert accepted.ok and accepted.value is not None
            await _wait_for(pilot, lambda: app.store.state.active_turns)
            await _open_sessions(pilot, app)
            await _wait_for(
                pilot,
                lambda: "●" in _row_label(app, running_id) and "●" not in _row_label(app, idle_id),
            )

    asyncio.run(drive())


def test_empty_state_renders_no_sessions(sessions_app_factory) -> None:
    app = sessions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_sessions(pilot, app)
            list_widget = app.query_one("#sessions-list", VerticalScroll)
            await _wait_for(pilot, lambda: list_widget.query_one_optional("#sessions-empty") is not None)
            assert list_widget.query_one("#sessions-empty", Static).content == "No sessions."

    asyncio.run(drive())
