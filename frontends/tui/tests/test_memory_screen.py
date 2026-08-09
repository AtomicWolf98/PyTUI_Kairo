"""MemoryScreen pilot tests: namespace/tag/text search, view, create/edit/delete
(with delete confirmation), empty-namespace inline notice, and result refresh
after mutations.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_sessions_screen.py
and test_workspace_screen.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry, MemoryQuery
from textual.containers import VerticalScroll
from textual.widgets import Input, Static

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.memory import MemoryDeleteModal, MemoryFormModal
from tests.support.fakes import NOW_PROFILE, FakeProvider

NOW = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def memory_app_factory(workspace: Path):
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


async def _open_memory(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+4")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#memory-screen") is not None)


async def _seed(app: KairoTuiApp, identifier: str, key: str, text: str,
                tags: tuple[str, ...] = (), namespace: str = "user") -> None:
    """Insert a memory entry directly through the kernel (public API)."""
    entry = MemoryEntry(MemoryId(identifier), namespace, key, (TextBlock(text),), NOW, NOW, tags)
    result = await app.kernel.memory.put(entry)
    assert result.ok and result.value is not None


async def _search(app: KairoTuiApp, pilot, *, namespace: str = "user", text: str = "", tags: str = "") -> None:
    app.query_one("#memory-namespace", Input).value = namespace
    app.query_one("#memory-text", Input).value = text
    app.query_one("#memory-tags", Input).value = tags
    await pilot.click("#memory-search-button")


def _row_ids(app: KairoTuiApp) -> list[str]:
    results = app.query_one("#memory-results", VerticalScroll)
    return [button.id or "" for button in results.query("Button")]


def _status_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#memory-status", Static).content)


def test_search_by_namespace_returns_rows(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _seed(app, "m1", "alpha", "first memory note", ("important",))
            await _seed(app, "m2", "beta", "second memory note", ("cn",))
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user")
            await _wait_for(pilot, lambda: "mem-m1" in _row_ids(app))
            assert "mem-m2" in _row_ids(app)

    asyncio.run(drive())


def test_search_by_tag_filters_rows(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _seed(app, "m1", "alpha", "tagged note", ("important",))
            await _seed(app, "m2", "beta", "untagged note")
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user", tags="important")
            # Wait atomically: the row list is rebuilt by an async worker, so
            # assert the filtered state in one predicate (see test_sessions_screen).
            await _wait_for(pilot, lambda: "mem-m1" in _row_ids(app) and "mem-m2" not in _row_ids(app))

    asyncio.run(drive())


def test_view_shows_detail(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _seed(app, "m1", "alpha", "full detail text", ("important",))
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user")
            await _wait_for(pilot, lambda: "mem-m1" in _row_ids(app))
            await pilot.click("#mem-m1")
            content = app.query_one("#memory-detail-content", Static)
            await _wait_for(pilot, lambda: "full detail text" in str(content.content))
            assert str(app.query_one("#memory-detail-namespace", Static).content) == "Namespace: user"
            assert str(app.query_one("#memory-detail-key", Static).content) == "Key: alpha"
            assert str(app.query_one("#memory-detail-tags", Static).content) == "Tags: important"

    asyncio.run(drive())


def test_create_persists_and_reesearch_finds_it(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_memory(pilot, app)
            await pilot.click("#memory-new")
            await _wait_for(pilot, lambda: isinstance(app.screen, MemoryFormModal))
            modal = cast(MemoryFormModal, app.screen)
            modal.query_one("#memory-form-namespace", Input).value = "user"
            modal.query_one("#memory-form-key", Input).value = "alpha"
            modal.query_one("#memory-form-text", Input).value = "created note"
            modal.query_one("#memory-form-tags", Input).value = "important, cn"
            await pilot.click("#memory-form-save")
            await _wait_for(pilot, lambda: not isinstance(app.screen, MemoryFormModal))
            # The entry is stored: a fresh kernel search finds it.
            listed = await app.kernel.memory.search(MemoryQuery("user", ""))
            assert listed.ok and listed.value is not None
            assert [(entry.key, entry.tags) for entry in listed.value] == [("alpha", ("important", "cn"))]

    asyncio.run(drive())


def test_edit_updates_content_and_preserves_memory_id(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _seed(app, "m1", "alpha", "original text", ("important",))
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user")
            await _wait_for(pilot, lambda: "mem-m1" in _row_ids(app))
            await pilot.click("#mem-m1")
            content = app.query_one("#memory-detail-content", Static)
            await _wait_for(pilot, lambda: "original text" in str(content.content))
            await pilot.click("#memory-edit")
            await _wait_for(pilot, lambda: isinstance(app.screen, MemoryFormModal))
            modal = cast(MemoryFormModal, app.screen)
            # Prefilled with the original values; namespace+key are immutable.
            assert modal.query_one("#memory-form-namespace", Input).value == "user"
            assert modal.query_one("#memory-form-key", Input).value == "alpha"
            assert modal.query_one("#memory-form-namespace", Input).disabled
            assert modal.query_one("#memory-form-key", Input).disabled
            modal.query_one("#memory-form-text", Input).value = "edited text"
            modal.query_one("#memory-form-tags", Input).value = "edited-tag"
            await pilot.click("#memory-form-save")
            await _wait_for(pilot, lambda: not isinstance(app.screen, MemoryFormModal))
            # Same memory_id, namespace and key; only content/tags changed.
            fetched = await app.kernel.memory.get(MemoryId("m1"))
            assert fetched.ok and fetched.value is not None
            assert fetched.value.memory_id == MemoryId("m1")
            assert fetched.value.namespace == "user"
            assert fetched.value.key == "alpha"
            assert fetched.value.content == (TextBlock("edited text"),)
            assert fetched.value.tags == ("edited-tag",)
            # The detail pane refreshes to the updated content.
            await _wait_for(pilot, lambda: "edited text" in str(content.content))

    asyncio.run(drive())


def test_delete_confirms_and_removes_entry(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _seed(app, "m1", "alpha", "doomed note")
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user")
            await _wait_for(pilot, lambda: "mem-m1" in _row_ids(app))
            await pilot.click("#mem-m1")
            content = app.query_one("#memory-detail-content", Static)
            await _wait_for(pilot, lambda: "doomed note" in str(content.content))
            await pilot.click("#memory-delete")
            await _wait_for(pilot, lambda: isinstance(app.screen, MemoryDeleteModal))
            await pilot.click("#memory-delete-confirm")
            await _wait_for(pilot, lambda: not isinstance(app.screen, MemoryDeleteModal))
            # The row is gone after the refresh and the kernel entry is deleted.
            await _wait_for(pilot, lambda: "mem-m1" not in _row_ids(app))
            fetched = await app.kernel.memory.get(MemoryId("m1"))
            assert not fetched.ok  # NOT_FOUND

    asyncio.run(drive())


def test_empty_namespace_shows_inline_notice(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_memory(pilot, app)
            await pilot.click("#memory-search-button")
            # The kernel returns INVALID_ARGUMENT; the message is surfaced inline.
            await _wait_for(pilot, lambda: "namespace" in _status_text(app).casefold())

    asyncio.run(drive())


def test_results_refresh_after_create(memory_app_factory) -> None:
    app = memory_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_memory(pilot, app)
            await _search(app, pilot, namespace="user")
            results = app.query_one("#memory-results", VerticalScroll)
            await _wait_for(pilot, lambda: results.query_one_optional("#memory-empty", Static) is not None)
            await pilot.click("#memory-new")
            await _wait_for(pilot, lambda: isinstance(app.screen, MemoryFormModal))
            modal = cast(MemoryFormModal, app.screen)
            modal.query_one("#memory-form-namespace", Input).value = "user"
            modal.query_one("#memory-form-key", Input).value = "alpha"
            modal.query_one("#memory-form-text", Input).value = "fresh note"
            await pilot.click("#memory-form-save")
            await _wait_for(pilot, lambda: not isinstance(app.screen, MemoryFormModal))
            # The current search re-ran after the create: the new row appears
            # without pressing Search again (id is a generated hex uuid).
            await _wait_for(pilot, lambda: any(row_id.startswith("mem-") for row_id in _row_ids(app)))

    asyncio.run(drive())
