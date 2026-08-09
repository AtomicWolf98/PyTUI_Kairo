"""Extensions page: Built-in Tools / Skills / MCP tabs.

- **Tools** pane lists ``kernel.tools.list()`` descriptors with a Reload button
  (``kernel.tools.reload()``).
- **Skills** pane shows the inventory status line (``trusted``/``untrusted``/
  ``changed``/``absent`` + ``digest[:12]`` + package count), package rows, and
  Trust (passes the inspected ``inventory.digest`` — a mismatch raises in the
  kernel and surfaces inline), Revoke and Reload.
- **MCP** pane renders the flat ``kernel.mcp.catalog()`` (guarded — it can
  raise) grouped by namespace, with Connect/Refresh and an Invoke button per
  tool/prompt row (Read for resources). Invocation parses JSON arguments via
  ``freeze_json(json.loads(text or "{}"))`` and routes to the typed facade
  calls; the JSON result renders in the result pane.

Approval surfacing (tui_plan: no auto-approval): a typed MCP call in a mode
that denies the external scope creates a pending interaction the EventPump
folds into ``store.pending_interactions`` — the Inspector Activity tab already
renders it with respond buttons. The page only shows an "Approval pending —
review in Activity" line (store-driven) whose button activates that tab; it
never calls ``interactions.respond`` itself. A POLICY_DENIED result surfaces
the facade's message as an error banner.
"""

from __future__ import annotations

import json
from typing import NamedTuple, cast

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.tools import ToolDescriptor
from kairo_kernel.errors import KernelResult
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TabbedContent, TabPane

from kairo_tui.extensions_model import mcp_entries, mcp_groups, skill_rows, tool_rows
from kairo_tui.store import AppState
from kairo_tui.structs import CatalogEntryLike, McpCatalogLike, SkillInventoryLike


class McpInvokeData(NamedTuple):
    qualified_name: str
    arguments: JsonObject


class McpInvokeModal(ModalScreen[McpInvokeData | None]):
    """JSON arguments input for a typed MCP tool/prompt call.

    Parses ``freeze_json(json.loads(text or "{}"))`` (JsonObject required);
    invalid JSON or a non-object payload keeps the modal open with an inline
    error instead of failing the facade call.
    """

    def __init__(self, qualified_name: str) -> None:
        super().__init__()
        self._qualified_name = qualified_name

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-invoke-modal"):
            yield Static(f"Invoke {self._qualified_name}", id="mcp-invoke-title")
            yield Input(placeholder='{"key": "value"}', id="mcp-invoke-arguments")
            yield Static("", id="mcp-invoke-error", markup=False)
            with Horizontal(id="mcp-invoke-actions"):
                yield Button("Run", id="mcp-invoke-run", variant="primary")
                yield Button("Cancel", id="mcp-invoke-cancel")

    def _submit(self) -> None:
        text = self.query_one("#mcp-invoke-arguments", Input).value
        try:
            frozen = freeze_json(json.loads(text or "{}"))
        except (ValueError, TypeError) as exc:
            self.query_one("#mcp-invoke-error", Static).update(f"Invalid JSON arguments: {exc}")
            return
        if not isinstance(frozen, JsonObject):
            self.query_one("#mcp-invoke-error", Static).update("Arguments must be a JSON object.")
            return
        self.dismiss(McpInvokeData(self._qualified_name, frozen))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mcp-invoke-run":
            self._submit()
        elif event.button.id == "mcp-invoke-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mcp-invoke-arguments":
            self._submit()


class ExtensionsScreen(Container):
    """Extensions page: three tabs over the tools/skills/mcp facades."""

    DEFAULT_CSS = """
    ExtensionsScreen {
        height: 1fr;
    }
    /* TabbedContent sizes to content by default; force the chain so the
       per-pane scroll lists get the page height. */
    ExtensionsScreen TabbedContent {
        height: 1fr;
    }
    ExtensionsScreen ContentSwitcher {
        height: 1fr;
    }
    ExtensionsScreen TabPane {
        height: 1fr;
    }
    #ext-tools-list, #ext-skills-list, #ext-mcp-list {
        height: 1fr;
    }
    /* The page area is only ~80 cols at the full breakpoint (nav 22 +
       inspector 38); several actions per pane overflow without min-width 0. */
    #ext-tools-actions Button, #ext-skills-actions Button, #ext-mcp-actions Button {
        min-width: 0;
    }
    .ext-mcp-row {
        height: auto;
    }
    .ext-mcp-name {
        width: 1fr;
    }
    .ext-mcp-row Button {
        width: 10;
        min-width: 0;
    }
    /* Horizontal defaults to height/width 1fr and auto-width Statics fill
       leftover space, which pushed the review button past the page edge into
       the inspector column — pin the row to its content and give the label
       1fr so the button stays inside the page. */
    #ext-review {
        height: auto;
        width: 1fr;
    }
    #ext-review-label {
        width: 1fr;
    }
    #ext-review-button {
        width: 20;
        min-width: 0;
    }
    #extensions-status.ext-error {
        color: $error;
    }
    #mcp-invoke-modal {
        width: 60;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, app) -> None:
        super().__init__(id="extensions-screen")
        self._app = app
        self.kernel = app.kernel
        self.store = app.store

    def compose(self) -> ComposeResult:
        yield Static("[b]Extensions[/b]", id="extensions-title")
        with TabbedContent():
            with TabPane("Built-in Tools", id="ext-tools-pane"):
                yield VerticalScroll(id="ext-tools-list")
                with Horizontal(id="ext-tools-actions"):
                    yield Button("Reload", id="ext-tools-reload")
            with TabPane("Skills", id="ext-skills-pane"):
                yield Static("", id="ext-skills-status")
                yield VerticalScroll(id="ext-skills-list")
                with Horizontal(id="ext-skills-actions"):
                    yield Button("Trust", id="ext-skills-trust", variant="primary")
                    yield Button("Revoke", id="ext-skills-revoke", variant="error")
                    yield Button("Reload", id="ext-skills-reload")
            with TabPane("MCP", id="ext-mcp-pane"):
                yield VerticalScroll(id="ext-mcp-list")
                with Horizontal(id="ext-mcp-actions"):
                    yield Button("Connect", id="ext-mcp-connect", variant="primary")
                    yield Button("Refresh", id="ext-mcp-refresh")
                yield Static("", id="ext-mcp-result", markup=False)
        with Horizontal(id="ext-review"):
            yield Static("Approval pending — review in Activity", id="ext-review-label")
            yield Button("Review in Activity", id="ext-review-button")
        yield Static("", id="extensions-status", markup=False)

    def on_mount(self) -> None:
        self.store.subscribe(self._on_store)
        self._render_review_line(self.store.state)
        self.run_worker(self._load_all())

    def on_unmount(self) -> None:
        self.store.unsubscribe(self._on_store)

    def _on_store(self, state: AppState) -> None:
        self._render_review_line(state)

    def _render_review_line(self, state: AppState) -> None:
        """Toggle the "review in Activity" affordance while an approval is pending."""
        review = self.query_one_optional("#ext-review")
        if review is not None:
            review.display = bool(state.pending_interactions)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "ext-tools-reload":
            self.run_worker(self._reload_tools())
        elif button_id == "ext-skills-trust":
            self.run_worker(self._trust_skills())
        elif button_id == "ext-skills-revoke":
            self.run_worker(self._revoke_skills())
        elif button_id == "ext-skills-reload":
            self.run_worker(self._reload_skills())
        elif button_id == "ext-mcp-connect":
            self.run_worker(self._connect_mcp())
        elif button_id == "ext-mcp-refresh":
            self.run_worker(self._refresh_mcp())
        elif button_id == "ext-review-button":
            self.run_worker(self._review_in_activity())
        elif button_id.startswith("ext-invoke-tools-"):
            self.run_worker(self._invoke("tools", button_id.removeprefix("ext-invoke-tools-")))
        elif button_id.startswith("ext-invoke-prompts-"):
            self.run_worker(self._invoke("prompts", button_id.removeprefix("ext-invoke-prompts-")))
        elif button_id.startswith("ext-read-"):
            self.run_worker(self._read(button_id.removeprefix("ext-read-")))

    async def _load_all(self) -> None:
        await self._load_tools()
        await self._load_skills()
        await self._load_mcp()

    # --- Built-in Tools pane ---

    async def _load_tools(self) -> None:
        result = await self.kernel.tools.list()
        if not result.ok:
            self._notice(result.error.message if result.error else "Tools list failed.")
            return
        await self._render_tools(result.value)

    async def _reload_tools(self) -> None:
        result = await self.kernel.tools.reload()
        if not result.ok:
            self._notice(result.error.message if result.error else "Tools reload failed.")
            return
        await self._render_tools(result.value)

    async def _render_tools(self, descriptors: tuple[ToolDescriptor, ...] | None) -> None:
        if descriptors is None:
            return
        container = self.query_one("#ext-tools-list", VerticalScroll)
        await container.remove_children()
        rows = tool_rows(descriptors)
        if not rows:
            await container.mount(Static("No built-in tools.", id="ext-tools-empty"))
            return
        for row in rows:
            await container.mount(Static(row))

    # --- Skills pane ---

    async def _load_skills(self) -> None:
        try:
            inventory = cast(SkillInventoryLike, await self.kernel.skills.inspect())
        except Exception as exc:
            self._notice(f"Skills unavailable: {exc}")
            return
        await self._render_skills(inventory)

    async def _render_skills(self, inventory: SkillInventoryLike) -> None:
        status = self.query_one("#ext-skills-status", Static)
        if inventory.status == "absent":
            status.update("Skills: absent")
        else:
            status.update(
                f"Skills: {inventory.status} — {inventory.digest[:12]} — {len(inventory.packages)} package(s)"
            )
        container = self.query_one("#ext-skills-list", VerticalScroll)
        await container.remove_children()
        rows = skill_rows(inventory)
        if not rows:
            await container.mount(Static("No skill packages.", id="ext-skills-empty"))
            return
        for row in rows:
            await container.mount(Static(row))

    async def _trust_skills(self) -> None:
        try:
            inventory = cast(SkillInventoryLike, await self.kernel.skills.inspect())
        except Exception as exc:
            self._notice(f"Skills unavailable: {exc}")
            return
        if not inventory.digest:
            self._notice("No skill packages to trust.")
            return
        try:
            result = await self.kernel.skills.trust(inventory.digest)
        except Exception as exc:
            self._notice(f"Skills trust failed: {exc}")
            return
        if result.ok and result.value is not None:
            await self._render_skills(cast(SkillInventoryLike, result.value))
        else:
            self._notice(result.error.message if result.error else "Skills trust failed.")

    async def _revoke_skills(self) -> None:
        result = await self.kernel.skills.revoke()
        if not result.ok:
            self._notice(result.error.message if result.error else "Skills revoke failed.")
            return
        await self._load_skills()

    async def _reload_skills(self) -> None:
        result = await self.kernel.skills.reload()
        if not result.ok:
            self._notice(result.error.message if result.error else "Skills reload failed.")
            return
        await self._load_skills()

    # --- MCP pane ---

    async def _load_mcp(self) -> None:
        try:
            catalog = self.kernel.mcp.catalog()
        except Exception as exc:
            self._notice(f"MCP catalog unavailable: {exc}")
            return
        await self._render_mcp(mcp_entries(catalog))

    async def _render_mcp(self, entries: tuple[CatalogEntryLike, ...]) -> None:
        container = self.query_one("#ext-mcp-list", VerticalScroll)
        await container.remove_children()
        groups = mcp_groups(entries)
        if not groups:
            await container.mount(Static("No MCP servers.", id="ext-mcp-empty"))
            return
        for namespace, names in groups.items():
            await container.mount(Static(f"[b]{namespace}[/b]", classes="ext-mcp-group"))
            for qualified in names:
                button = (
                    Button("Read", id=f"ext-read-{qualified}")
                    if namespace == "resources"
                    else Button("Invoke", id=f"ext-invoke-{namespace}-{qualified}")
                )
                await container.mount(
                    Horizontal(Static(qualified, classes="ext-mcp-name"), button, classes="ext-mcp-row")
                )

    async def _connect_mcp(self) -> None:
        result = await self.kernel.mcp.connect()
        if not result.ok:
            self._notice(result.error.message if result.error else "MCP connect failed.")
            return
        connected = cast(tuple[McpCatalogLike, ...], result.value or ())
        self._notice(f"Connected: {len(connected)} server(s).")
        await self._load_mcp()

    async def _refresh_mcp(self) -> None:
        result = await self.kernel.mcp.refresh()
        if not result.ok:
            self._notice(result.error.message if result.error else "MCP refresh failed.")
            return
        catalogs = cast(tuple[McpCatalogLike, ...], result.value or ())
        self._notice(f"Refreshed: {len(catalogs)} server(s).")
        await self._load_mcp()

    # --- Typed MCP invocation ---

    async def _invoke(self, kind: str, qualified_name: str) -> None:
        data = await self._app.push_screen_wait(McpInvokeModal(qualified_name))
        if data is None:
            return
        if kind == "tools":
            result = await self.kernel.mcp.call_tool(data.qualified_name, data.arguments)
        else:
            result = await self.kernel.mcp.render_prompt(data.qualified_name, data.arguments)
        await self._handle_mcp_result(result)

    async def _read(self, qualified_name: str) -> None:
        result = await self.kernel.mcp.read_resource(qualified_name)
        await self._handle_mcp_result(result)

    async def _handle_mcp_result(self, result: KernelResult[JsonObject]) -> None:
        if not self.is_mounted:
            return
        status = self.query_one("#extensions-status", Static)
        if result.ok and result.value is not None:
            self.query_one("#ext-mcp-result", Static).update(
                json.dumps(thaw_json(result.value), indent=2, ensure_ascii=False)
            )
            status.update("")
            status.set_classes("")
            return
        message = result.error.message if result.error else "MCP operation failed."
        status.update(message)
        # POLICY_DENIED is the approval-rejected case: render it as an error
        # banner; the (now resolved) interaction was reviewed in Activity.
        status.set_classes(
            "ext-error" if result.error is not None and result.error.code is ErrorCode.POLICY_DENIED else ""
        )

    async def _review_in_activity(self) -> None:
        inspector = self._app.query_one("#inspector")
        tabbed = inspector.query_one(TabbedContent)
        tabbed.active = "activity"

    def _notice(self, message: str) -> None:
        status = self.query_one_optional("#extensions-status", Static)
        if status is not None:
            status.update(message)
