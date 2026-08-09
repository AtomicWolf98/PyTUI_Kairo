"""ExtensionsScreen pilot tests: tools list/reload, skills status/trust/revoke,
MCP catalog empty state + connect/refresh, typed invocation with MANUAL-mode
POLICY_DENIED surfacing (error banner + Activity review path, no auto-approval)
and AUTO-mode success rendering the JSON result.

The app is bootstrapped synchronously (outside any event loop) and driven via
the Pilot inside ``asyncio.run`` — the same pattern as test_memory_screen.py.
Two kernel seams are swapped post-bootstrap (test-local, never in kairo_tui):
the skill trust store is moved outside the workspace (the kernel rejects an
inside-workspace store), and the MCP hub is replaced with a structural fake
exposing one tool so typed invocation exercises the real facade approval path.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import cast

import pytest
from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.mcp import CatalogEntry, McpCatalog, McpHub, qualified_name
from kairo_kernel.skills import SkillTrustStore
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static, TabbedContent

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.screens.extensions import McpInvokeModal
from tests.support.fakes import NOW_PROFILE, FakeProvider, FakeTool, FakeToolRegistry


class CountingToolRegistry(FakeToolRegistry):
    """FakeToolRegistry that counts reload() calls (test-only verification)."""

    def __init__(self, *tools: FakeTool) -> None:
        super().__init__(*tools)
        self.reloads = 0

    async def reload(self):
        self.reloads += 1
        return await super().reload()


class _FakeMcpClient:
    """Structural McpClient surface the facade touches: catalog + typed calls."""

    def __init__(self, server_name: str, *, tools=(), resources=(), prompts=()) -> None:
        self.catalog = McpCatalog(server_name, tools, resources, prompts)
        self.connected = False
        self.tool_calls: list[tuple[str, dict[str, object]]] = []

    async def connect(self) -> McpCatalog:
        self.connected = True
        return self.catalog

    async def refresh(self) -> McpCatalog:
        return self.catalog

    async def call_tool(self, qualified: str, arguments: dict[str, object]) -> dict[str, object]:
        self.tool_calls.append((qualified, arguments))
        return {"echo": arguments.get("text", "")}

    async def read_resource(self, qualified: str) -> dict[str, object]:
        return {"resource": qualified}

    async def get_prompt(self, qualified: str, arguments: dict[str, object]) -> dict[str, object]:
        return {"prompt": qualified}


class _FakeMcpHub:
    """Structural McpHub surface the facade touches: catalog/connect_all/refresh_all."""

    def __init__(self, *clients: _FakeMcpClient) -> None:
        self.clients = clients
        self.connect_count = 0
        self.refresh_count = 0

    def catalog(self) -> tuple[CatalogEntry, ...]:
        entries = tuple(entry for client in self.clients for entry in client.catalog.all_entries())
        return tuple(sorted(entries, key=lambda item: item.qualified_name))

    async def connect_all(self) -> tuple[McpCatalog, ...]:
        self.connect_count += 1
        return tuple(await asyncio.gather(*(client.connect() for client in self.clients)))

    async def refresh_all(self) -> tuple[McpCatalog, ...]:
        self.refresh_count += 1
        return tuple(await asyncio.gather(*(client.refresh() for client in self.clients)))

    async def close(self) -> None:
        pass


ECHO_QUALIFIED = qualified_name("fake", "tools", "echo")
ECHO_BUTTON = f"#ext-invoke-tools-{ECHO_QUALIFIED}"


def _echo_hub() -> _FakeMcpHub:
    client = _FakeMcpClient(
        "fake",
        tools=(CatalogEntry("tools", "echo", ECHO_QUALIFIED, {"name": "echo"}),),
    )
    return _FakeMcpHub(client)


def _write_skill(workspace: Path, name: str = "echo") -> None:
    root = workspace / ".kairo" / "skills" / name
    root.mkdir(parents=True)
    (root / "skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": f"{name} description",
                "entrypoint": "SKILL.md",
                "permissions": ["workspace:read"],
            }
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text("# Echo", encoding="utf-8")


def _trust_key(workspace: Path) -> str:
    """Replicates the kernel's SkillTrustStore workspace key (normcase on nt)."""
    value = str(workspace.expanduser().resolve())
    return os.path.normcase(value) if os.name == "nt" else value


def _seed_stale_trust(store: SkillTrustStore, workspace: Path, digest: str = "stale") -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": 1, "entries": {_trust_key(workspace): digest}}),
        encoding="utf-8",
    )


@pytest.fixture
def extensions_app_factory(workspace: Path):
    """A booted KairoTuiApp with a seeded config; optional tools/hub/trust seams."""

    def make(
        *,
        provider=None,
        tools=None,
        mcp_hub: object | None = None,
        trust_store: SkillTrustStore | None = None,
        size: tuple[int, int] = (140, 40),
    ) -> KairoTuiApp:
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
        app = KairoTuiApp(bootstrap)
        if trust_store is not None:
            # The kernel rejects a trust store inside the workspace, but the
            # TUI bootstrap wires one there — swap for an outside store so the
            # trust flow is exercisable (see the report for the bootstrap bug).
            app.kernel.skills._registry.trust_store = trust_store
        if mcp_hub is not None:
            app.kernel.mcp._hub = cast(McpHub, mcp_hub)
        return app

    return make


async def _wait_for(pilot, predicate, *, polls: int = 80, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return


async def _open_extensions(pilot, app: KairoTuiApp) -> None:
    await pilot.press("ctrl+5")
    await pilot.pause()
    await _wait_for(pilot, lambda: app.query_one_optional("#extensions-screen") is not None)


def _extensions_tabbed(app: KairoTuiApp) -> TabbedContent:
    return app.query_one("#extensions-screen").query_one(TabbedContent)


def _status_text(app: KairoTuiApp) -> str:
    return str(app.query_one("#extensions-status", Static).content)


def _pending_id(app: KairoTuiApp) -> str | None:
    pending = app.store.state.pending_interactions
    return str(pending[0].interaction_id) if pending else None


async def _invoke_echo(pilot, app: KairoTuiApp, arguments: str = '{"text": "hello"}') -> None:
    await pilot.click(ECHO_BUTTON)
    await _wait_for(pilot, lambda: isinstance(app.screen, McpInvokeModal))
    modal = cast(McpInvokeModal, app.screen)
    modal.query_one("#mcp-invoke-arguments", Input).value = arguments
    await pilot.click("#mcp-invoke-run")
    await pilot.pause()
    await _wait_for(pilot, lambda: _pending_id(app) is not None)


def _inspector_tabbed(app: KairoTuiApp) -> TabbedContent:
    return app.query_one("#inspector", VerticalScroll).query_one(TabbedContent)


async def _respond_in_activity(pilot, app: KairoTuiApp, action: str) -> None:
    """Activate the Activity tab, wait for the pending card's button, click it.

    The inspector re-renders the pane on every store change and the 1 s tick,
    recreating the buttons — resolve the widget id after the pane settled.
    """
    _inspector_tabbed(app).active = "activity"
    await pilot.pause()
    button_id = f"#act-{_pending_id(app)}-{action}"
    await _wait_for(pilot, lambda: app.query_one_optional(button_id, Button) is not None)
    await pilot.click(button_id)
    await _wait_for(pilot, lambda: _pending_id(app) is None)


def test_tools_tab_lists_registry_descriptors(extensions_app_factory) -> None:
    registry = CountingToolRegistry(FakeTool("read_file"), FakeTool("search_file"))
    app = extensions_app_factory(tools=registry)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            container = app.query_one("#ext-tools-list", VerticalScroll)
            await _wait_for(
                pilot,
                lambda: any(str(s.content) == "read_file — read_file" for s in container.query("Static")),
            )
            labels = {str(s.content) for s in container.query("Static")}
            assert {"read_file — read_file", "search_file — search_file"} <= labels

    asyncio.run(drive())


def test_tools_reload_re_runs_registry(extensions_app_factory) -> None:
    registry = CountingToolRegistry(FakeTool("read_file"))
    app = extensions_app_factory(tools=registry)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            await _wait_for(pilot, lambda: registry.reloads == 0)
            await pilot.click("#ext-tools-reload")
            await _wait_for(pilot, lambda: registry.reloads == 1)

    asyncio.run(drive())


def test_skills_tab_shows_absent_without_skills_dir(extensions_app_factory) -> None:
    app = extensions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-skills-pane"
            await pilot.pause()
            status = app.query_one("#ext-skills-status", Static)
            await _wait_for(pilot, lambda: "absent" in str(status.content))
            assert str(status.content) == "Skills: absent"

    asyncio.run(drive())


def test_skills_trust_changed_to_trusted_with_digest(extensions_app_factory, workspace: Path) -> None:
    _write_skill(workspace)
    store = SkillTrustStore(workspace.parent / "skills-trust.json")
    _seed_stale_trust(store, workspace)  # stale digest → inspect reports "changed"
    app = extensions_app_factory(trust_store=store)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-skills-pane"
            await pilot.pause()
            status = app.query_one("#ext-skills-status", Static)
            await _wait_for(pilot, lambda: "changed" in str(status.content))
            inventory = await app.kernel.skills.inspect()
            assert inventory.status == "changed"
            assert inventory.digest[:12] in str(status.content)
            await pilot.click("#ext-skills-trust")
            await _wait_for(pilot, lambda: "trusted" in str(status.content))
            # Trust stored the exact inspected digest (the flow uses it verbatim).
            assert store.trusted_digest(workspace) == inventory.digest
            assert (await app.kernel.skills.inspect()).status == "trusted"

    asyncio.run(drive())


def test_skills_revoke_returns_to_untrusted(extensions_app_factory, workspace: Path) -> None:
    _write_skill(workspace)
    store = SkillTrustStore(workspace.parent / "skills-trust.json")
    _seed_stale_trust(store, workspace)
    app = extensions_app_factory(trust_store=store)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-skills-pane"
            await pilot.pause()
            status = app.query_one("#ext-skills-status", Static)
            await _wait_for(pilot, lambda: "changed" in str(status.content))
            await pilot.click("#ext-skills-trust")
            await _wait_for(pilot, lambda: "trusted" in str(status.content))
            await pilot.click("#ext-skills-revoke")
            await _wait_for(pilot, lambda: "untrusted" in str(status.content))
            assert store.trusted_digest(workspace) == ""
            assert (await app.kernel.skills.inspect()).status == "untrusted"

    asyncio.run(drive())


def test_mcp_tab_empty_state_with_empty_hub(extensions_app_factory) -> None:
    app = extensions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            container = app.query_one("#ext-mcp-list", VerticalScroll)
            await _wait_for(
                pilot,
                lambda: container.query_one_optional("#ext-mcp-empty", Static) is not None,
            )
            assert container.query_one("#ext-mcp-empty", Static).content == "No MCP servers."

    asyncio.run(drive())


def test_mcp_connect_and_refresh_with_empty_client_list(extensions_app_factory) -> None:
    app = extensions_app_factory()

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            await pilot.click("#ext-mcp-connect")
            await _wait_for(pilot, lambda: "Connected: 0" in _status_text(app))
            await pilot.click("#ext-mcp-refresh")
            await _wait_for(pilot, lambda: "Refreshed: 0" in _status_text(app))

    asyncio.run(drive())


def test_mcp_invoke_policy_denied_shows_banner(extensions_app_factory) -> None:
    hub = _echo_hub()
    app = extensions_app_factory(mcp_hub=hub)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            await _wait_for(pilot, lambda: app.query_one_optional(ECHO_BUTTON, Button) is not None)
            await _invoke_echo(pilot, app)
            # The broker's request is pending; reject it (never auto-approved).
            await _respond_in_activity(pilot, app, "reject")
            await _wait_for(pilot, lambda: "did not authorize external scope" in _status_text(app))
            assert "MCP operation rejected" in _status_text(app)

    asyncio.run(drive())


def test_pending_interaction_appears_in_activity_with_approve_button(extensions_app_factory) -> None:
    hub = _echo_hub()
    app = extensions_app_factory(mcp_hub=hub)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            await _wait_for(pilot, lambda: app.query_one_optional(ECHO_BUTTON, Button) is not None)
            await _invoke_echo(pilot, app)
            # The page surfaces the pending state with a review affordance.
            review = app.query_one("#ext-review")
            await _wait_for(pilot, lambda: review.display is True)
            assert "Approval pending" in str(app.query_one("#ext-review-label", Static).content)
            # The Inspector Activity tab rendered the pending card with respond buttons.
            approve_id = f"#act-{_pending_id(app)}-approve"
            await _wait_for(pilot, lambda: app.query_one_optional(approve_id, Button) is not None)
            activity = _inspector_tabbed(app).get_pane("activity")
            assert activity.query_one(approve_id, Button) is not None
            # Clean up: resolve the pending interaction (no auto-approval).
            await _respond_in_activity(pilot, app, "reject")

    asyncio.run(drive())


def test_review_in_activity_switches_inspector_tab(extensions_app_factory) -> None:
    hub = _echo_hub()
    app = extensions_app_factory(mcp_hub=hub)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            await _wait_for(pilot, lambda: app.query_one_optional(ECHO_BUTTON, Button) is not None)
            await _invoke_echo(pilot, app)
            await _wait_for(pilot, lambda: app.query_one("#ext-review").display is True)
            await pilot.click("#ext-review-button")
            await _wait_for(pilot, lambda: _inspector_tabbed(app).active == "activity")
            assert _inspector_tabbed(app).active == "activity"
            # The Activity tab is now showing the pending card; resolve it.
            await _respond_in_activity(pilot, app, "reject")

    asyncio.run(drive())


def test_mcp_invoke_succeeds_under_auto_and_renders_json(extensions_app_factory) -> None:
    hub = _echo_hub()
    app = extensions_app_factory(mcp_hub=hub)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            # Patch authorization mode first; AUTO still requires approval for
            # the EXTERNAL scope, and the typed call proceeds once approved.
            snapshot = await app.kernel.preferences.snapshot()
            patched = await app.kernel.preferences.patch(
                PreferencesPatch(snapshot.revision, authorization_mode=AuthorizationMode.AUTO)
            )
            assert patched.ok
            await _open_extensions(pilot, app)
            _extensions_tabbed(app).active = "ext-mcp-pane"
            await pilot.pause()
            await _wait_for(pilot, lambda: app.query_one_optional(ECHO_BUTTON, Button) is not None)
            await _invoke_echo(pilot, app, arguments='{"text": "hello"}')
            await _respond_in_activity(pilot, app, "approve")
            result = app.query_one("#ext-mcp-result", Static)
            await _wait_for(pilot, lambda: "hello" in str(result.content))
            assert '"echo": "hello"' in str(result.content)
            assert hub.clients[0].tool_calls == [(ECHO_QUALIFIED, {"text": "hello"})]

    asyncio.run(drive())
