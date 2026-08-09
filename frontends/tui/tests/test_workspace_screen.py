"""WorkspaceScreen pilot tests: lazy tree, preview, git changed files + diff,
bookmarks, switch, BUSY surfacing, stale-revision drop, non-git and empty states.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_sessions_screen.py,
because pytest-asyncio's auto-mode loop rejects the nested ``asyncio.run``
inside ``build_running_kernel``. Git workspaces are created with ``git init``
in the tmp workspace (the kernel shells out to git internally; the TUI never
runs git).
"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from kairo_kernel.contracts.enums import EventType
from kairo_kernel.contracts.events import ChangeEvent, KernelEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, SessionId
from kairo_kernel.contracts.turns import TurnRequest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.workspace import WorkspaceTreeWidget
from kairo_tui.store import EventAction
from kairo_tui.workspace_model import change_button_id
from tests.support.fakes import NOW_PROFILE, FakeProvider


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *arguments],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def workspace_app_factory(workspace: Path):
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


async def _wait_for(pilot, predicate, *, polls: int = 80, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


async def _open_workspace(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+3")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#workspace-screen") is not None)


def _tree(app: KairoTuiApp) -> WorkspaceTreeWidget:
    return app.query_one("#workspace-tree", WorkspaceTreeWidget)


def _node(app: KairoTuiApp, data: str):
    return next(child for child in _tree(app).root.children if child.data == data)


async def _wait_node(app: KairoTuiApp, pilot, data: str):
    """Wait for the root child with the given relative path (bootstrap also
    creates ``.kairo/`` in the workspace, so never assert exact child counts)."""
    tree = _tree(app)
    await _wait_for(pilot, lambda: any(child.data == data for child in tree.root.children))
    return next(child for child in tree.root.children if child.data == data)


def _preview_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#workspace-preview", Static).content)


def _status_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#workspace-status", Static).content)


def _workspace_changed_event(sequence: int) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.WORKSPACE_CHANGED, ChangeEvent(revision=sequence),
    )


def test_tree_lists_root_entries(workspace_app_factory, workspace: Path) -> None:
    (workspace / "a.txt").write_text("aa", encoding="utf-8")
    (workspace / "subdir").mkdir()
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            tree = _tree(app)
            await _wait_for(pilot, lambda: len(tree.root.children) >= 2)
            # bootstrap also creates .kairo/, so assert the expected entries are
            # present rather than matching the exact set.
            assert {"a.txt", "subdir"} <= {str(child.data) for child in tree.root.children}
            subdir = _node(app, "subdir")
            assert len(subdir.children) == 1  # "…" placeholder so the arrow shows

    asyncio.run(drive())


def test_expanding_directory_fetches_children_lazily(workspace_app_factory, workspace: Path) -> None:
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "inner.txt").write_text("in", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            subdir = await _wait_node(app, pilot, "subdir")
            # Not loaded yet: only the placeholder leaf exists, never the file.
            assert not any(child.data == "subdir/inner.txt" for child in subdir.children)
            subdir.expand()
            await _wait_for(pilot, lambda: any(child.data == "subdir/inner.txt" for child in subdir.children))

    asyncio.run(drive())


def test_file_preview_shows_text(workspace_app_factory, workspace: Path) -> None:
    (workspace / "notes.txt").write_text("hello workspace", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            notes = await _wait_node(app, pilot, "notes.txt")
            _tree(app).select_node(notes)
            await _wait_for(pilot, lambda: "hello workspace" in _preview_text(app))

    asyncio.run(drive())


def test_directory_preview_shows_children(workspace_app_factory, workspace: Path) -> None:
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "inner.txt").write_text("in", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            subdir = await _wait_node(app, pilot, "subdir")
            _tree(app).select_node(subdir)
            await _wait_for(pilot, lambda: "inner.txt" in _preview_text(app))

    asyncio.run(drive())


def test_changed_files_renders_git_status_rows(workspace_app_factory, workspace: Path) -> None:
    _git(workspace, "init")
    (workspace / "tracked.txt").write_text("one", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "initial")
    (workspace / "tracked.txt").write_text("two", encoding="utf-8")
    (workspace / "fresh.txt").write_text("new", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            tracked_id = f"#chg-{change_button_id('tracked.txt')}"
            fresh_id = f"#chg-{change_button_id('fresh.txt')}"
            await _wait_for(pilot, lambda: app.query_one_optional(tracked_id, Button) is not None)
            assert str(app.query_one(tracked_id, Button).label) == "M tracked.txt"
            assert str(app.query_one(fresh_id, Button).label) == "U fresh.txt"

    asyncio.run(drive())


def test_diff_shows_unified_diff(workspace_app_factory, workspace: Path) -> None:
    _git(workspace, "init")
    (workspace / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "initial")
    (workspace / "tracked.txt").write_text("two\n", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            tracked_id = f"#chg-{change_button_id('tracked.txt')}"
            await _wait_for(pilot, lambda: app.query_one_optional(tracked_id, Button) is not None)
            await pilot.click(tracked_id)
            diff = app.query_one("#workspace-diff", Static)
            await _wait_for(pilot, lambda: "@@" in str(diff.content) and "+two" in str(diff.content))

    asyncio.run(drive())


def test_untracked_file_shows_untracked_marker(workspace_app_factory, workspace: Path) -> None:
    _git(workspace, "init")
    (workspace / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "initial")
    (workspace / "fresh.txt").write_text("new\n", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            fresh_id = f"#chg-{change_button_id('fresh.txt')}"
            await _wait_for(pilot, lambda: app.query_one_optional(fresh_id, Button) is not None)
            await pilot.click(fresh_id)
            diff = app.query_one("#workspace-diff", Static)
            await _wait_for(pilot, lambda: "untracked" in str(diff.content))
            assert str(diff.content) == "untracked"  # empty diff + marker

    asyncio.run(drive())


def test_bookmark_save_appears_in_list(workspace_app_factory) -> None:
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            app.query_one("#workspace-bookmark-name", Input).value = "home"
            app.query_one("#workspace-bookmark-path", Input).value = "subdir"
            await pilot.click("#workspace-bookmark-save")
            await _wait_for(pilot, lambda: app.query_one_optional("#bm-remove-home", Button) is not None)
            snapshot = await app.kernel.workspace.snapshot()
            assert [(bookmark.name, bookmark.path) for bookmark in snapshot.bookmarks] == [("home", "subdir")]

    asyncio.run(drive())


def test_bookmark_remove_disappears(workspace_app_factory) -> None:
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            app.query_one("#workspace-bookmark-name", Input).value = "home"
            app.query_one("#workspace-bookmark-path", Input).value = "subdir"
            await pilot.click("#workspace-bookmark-save")
            await _wait_for(pilot, lambda: app.query_one_optional("#bm-remove-home", Button) is not None)
            await pilot.click("#bm-remove-home")
            await _wait_for(pilot, lambda: app.query_one_optional("#bm-remove-home", Button) is None)
            snapshot = await app.kernel.workspace.snapshot()
            assert snapshot.bookmarks == ()

    asyncio.run(drive())


def test_move_updates_store_and_records_recent(workspace_app_factory, workspace: Path) -> None:
    second = workspace.parent / "second-workspace"
    second.mkdir()
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            app.query_one("#workspace-switch-target", Input).value = str(second)
            await pilot.click("#workspace-move")
            await _wait_for(pilot, lambda: app.store.state.workspace_root == str(second.resolve()))
            assert app.store.state.workspace_revision == 1
            assert app.store.state.document.recent_workspaces == (str(second.resolve()),)

    asyncio.run(drive())


def test_move_while_turn_active_surfaces_busy(workspace_app_factory, workspace: Path) -> None:
    provider = FakeProvider(block=True)
    app = workspace_app_factory(provider=provider)
    second = workspace.parent / "second-workspace"
    second.mkdir()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            created = await app.kernel.sessions.create("Blocking")
            assert created.ok and created.value is not None
            session_id = str(created.value.session_id)
            accepted = await app.kernel.submit(TurnRequest("block me", session_id=SessionId(session_id)))
            assert accepted.ok and accepted.value is not None
            await _wait_for(pilot, lambda: app.store.state.active_turns)
            await _open_workspace(pilot, app)
            app.query_one("#workspace-switch-target", Input).value = str(second)
            await pilot.click("#workspace-move")
            await _wait_for(pilot, lambda: "turn is active" in _status_text(app))
            assert app.store.state.workspace_root != str(second.resolve())  # move did not happen

    asyncio.run(drive())


def test_stale_response_is_dropped(workspace_app_factory, workspace: Path) -> None:
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "inner.txt").write_text("in", encoding="utf-8")
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            subdir = await _wait_node(app, pilot, "subdir")
            # A newer WORKSPACE_CHANGED bumps the store revision to 1 while the
            # kernel still serves revision 0: every later fetch is stale.
            app.store.dispatch(EventAction(_workspace_changed_event(1)))
            subdir.expand()
            for _ in range(10):
                await pilot.pause(0.05)
            # The lazy fetch landed but was dropped: the placeholder was never
            # replaced and the inner file never appeared.
            assert not any(child.data == "subdir/inner.txt" for child in subdir.children)
            assert len(subdir.children) == 1
            assert subdir.children[0].data is None

    asyncio.run(drive())


def test_non_git_workspace_shows_not_a_repository_notice(workspace_app_factory) -> None:
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            container = app.query_one("#workspace-changes", VerticalScroll)
            await _wait_for(
                pilot, lambda: container.query_one_optional("#workspace-changes-empty", Static) is not None
            )
            notice = container.query_one("#workspace-changes-empty", Static)
            assert "git repository" in str(notice.content).casefold()

    asyncio.run(drive())


def test_bookmark_empty_state(workspace_app_factory) -> None:
    app = workspace_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_workspace(pilot, app)
            container = app.query_one("#workspace-bookmark-list", VerticalScroll)
            await _wait_for(
                pilot, lambda: container.query_one_optional("#workspace-bookmarks-empty", Static) is not None
            )
            assert container.query_one("#workspace-bookmarks-empty", Static).content == "No bookmarks."

    asyncio.run(drive())
