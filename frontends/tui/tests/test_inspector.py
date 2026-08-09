"""Inspector Activity tab: pending interactions rendered with respond actions.

The inspector mirrors the timeline's respond controls (ids ``act-{id}-{action}``)
so approvals can be driven from the Activity tab; the 1 s countdown is
display-only. Same synchronous-bootstrap + ``asyncio.run`` Pilot pattern as
test_chat_screen.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from kairo_kernel.contracts.content import TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import EventType, ProviderStreamKind
from kairo_kernel.contracts.events import ChangeEvent, KernelEvent
from kairo_kernel.contracts.identifiers import EventId, KernelId, ToolCallId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.providers import ProviderStreamEvent
from textual.containers import VerticalScroll
from textual.widgets import Button, Static, TabbedContent

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import EventAction
from kairo_tui.widgets import Composer
from tests.support.fakes import NOW_PROFILE, FakeProvider, FakeTool, FakeToolRegistry


@pytest.fixture
def inspector_app_factory(workspace: Path):
    def make(*, provider=None, tools=None) -> KairoTuiApp:
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
            tools=tools,
        )
        return KairoTuiApp(bootstrap)
    return make


def _tool_call_script(name: str) -> tuple[ProviderStreamEvent, ...]:
    call = ToolCallBlock(ToolCallId(name), name, JsonObject.from_pairs(("path", "README.md")))
    return (
        ProviderStreamEvent(ProviderStreamKind.TOOL_CALL, tool_call=call),
        ProviderStreamEvent(ProviderStreamKind.COMPLETED),
    )


def _chat_round(text: str) -> tuple[ProviderStreamEvent, ...]:
    return (
        ProviderStreamEvent(ProviderStreamKind.CONTENT, (TextBlock(text),)),
        ProviderStreamEvent(ProviderStreamKind.COMPLETED),
    )


async def _submit_via_composer(pilot, app: KairoTuiApp, text: str) -> None:
    await pilot.click("#composer")
    app.query_one("#composer", Composer).focus()
    await pilot.press(*tuple(text))
    await pilot.press("enter")


async def _wait_for(pilot, predicate, *, polls: int = 60, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


def test_inspector_lists_pending_interaction(inspector_app_factory) -> None:
    """A pending tool approval appears in #activity with an act-…-approve button."""
    provider = FakeProvider(_tool_call_script("read_file"), _chat_round("done"))
    tool = FakeTool("read_file")
    app = inspector_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            activity = app.query_one("#activity")
            await _wait_for(pilot, lambda: activity.query(".act-approve"))
            prompt = activity.query_one(".act-prompt", Static)
            assert "read_file" in str(prompt.content)
            approve = activity.query_one(".act-approve", Button)
            pending = await app.kernel.interactions.pending()
            assert len(pending) == 1
            assert approve.id == f"act-{pending[0].interaction_id}-approve"
            assert approve.label.plain == "Run once"
            countdown = activity.query_one(".act-countdown", Static)
            assert "expires in" in str(countdown.content)

    asyncio.run(drive())


def _workspace_event(sequence: int, revision: int, summary: str) -> KernelEvent:
    return KernelEvent(
        EventId(f"e{sequence}"), KernelId("k1"), sequence, datetime.now(timezone.utc),
        EventType.WORKSPACE_CHANGED, ChangeEvent(revision, "workspace", summary),
    )


def _changes_text(app: KairoTuiApp) -> str:
    changes = app.query_one("#changes")
    return " ".join(str(item.content) for item in changes.query(Static))


def test_inspector_changes_tab_shows_workspace_revisions(inspector_app_factory) -> None:
    """The Changes tab lists WORKSPACE_CHANGED revisions newest-first plus the
    store's current workspace_revision."""
    app = inspector_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            changes = app.query_one("#changes")
            await _wait_for(pilot, lambda: "changes-footer" in {w.id or "" for w in changes.query(Static)})
            assert "No workspace changes." in _changes_text(app)
            app.store.dispatch(EventAction(_workspace_event(1, 7, "Files changed by write_file.")))
            app.store.dispatch(EventAction(_workspace_event(2, 9, "Files changed by edit_file.")))
            await _wait_for(pilot, lambda: "r9" in _changes_text(app))
            rows = list(changes.query(".changes-row"))
            assert len(rows) == 2
            # Newest first: revision 9's row precedes revision 7's.
            assert "r9" in str(rows[0].content) and "Files changed by edit_file." in str(rows[0].content)
            assert "r7" in str(rows[1].content) and "Files changed by write_file." in str(rows[1].content)
            assert f"current revision: {app.store.state.workspace_revision}" in _changes_text(app)

    asyncio.run(drive())


def test_inspector_respond_resolves_card(inspector_app_factory) -> None:
    """Approving from the Activity tab executes the tool and updates the timeline card."""
    provider = FakeProvider(_tool_call_script("read_file"), _chat_round("done"))
    tool = FakeTool("read_file")
    app = inspector_app_factory(provider=provider, tools=FakeToolRegistry(tool))

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _submit_via_composer(pilot, app, "read the file")
            activity = app.query_one("#activity")
            await _wait_for(pilot, lambda: activity.query(".act-approve"))
            pending = await app.kernel.interactions.pending()
            assert len(pending) == 1
            interaction_id = str(pending[0].interaction_id)
            # Make the Activity tab visible so the button can be clicked.
            app.query_one(TabbedContent).active = "activity"
            await pilot.pause()
            await pilot.click(f"#act-{interaction_id}-approve")
            await _wait_for(pilot, lambda: not app.store.state.pending_interactions)
            assert len(await app.kernel.interactions.pending()) == 0
            timeline = app.query_one("#chat-timeline", VerticalScroll)
            text = timeline.query_one(".tool-card-text", Static)
            await _wait_for(pilot, lambda: "succeeded" in str(text.content))
            assert tool.calls == 1

    asyncio.run(drive())
