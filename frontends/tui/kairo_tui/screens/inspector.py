"""Right inspector panel: Context / Activity / Changes tabs.

Activity renders the pending interactions (tool/plan/text) with the same
respond actions as the chat timeline (ids ``act-{interaction_id}-{action}``)
plus a display-only 1 s countdown. Changes lists WORKSPACE_CHANGED events
newest-first (revision + summary) plus the store's current workspace_revision.
No branch ever calls ``respond`` on expiry — the kernel broker resolves expiry
fail-closed with the request's ``safe_default``.
"""

from __future__ import annotations

from kairo_kernel.contracts.enums import EventType, InteractionAction, InteractionKind
from kairo_kernel.contracts.events import ChangeEvent
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Input, Static, TabbedContent, TabPane

from kairo_tui.chat_model import countdown_seconds
from kairo_tui.screens.chat import PlanEditModal


class ActivityCard(Container):
    """One pending interaction in the Activity tab: prompt, countdown, respond buttons.

    The countdown is recomputed whenever the pane re-renders (every store
    change and 1 s tick); it is display-only — no branch responds on expiry.
    """

    def __init__(self, request: InteractionRequest) -> None:
        super().__init__(classes="act-card")
        self._request = request

    def compose(self) -> ComposeResult:
        request = self._request
        yield Static(f"[b]{request.kind.value}[/b] — {request.prompt}", classes="act-prompt")
        countdown = countdown_seconds(request.expires_at)
        if countdown is not None:
            yield Static(f"expires in {countdown:g}s", classes="act-countdown")
        if request.kind is InteractionKind.TOOL_APPROVAL:
            yield Button("Run once", id=f"act-{request.interaction_id}-approve", classes="act-approve", variant="primary")
            yield Button("Reject", id=f"act-{request.interaction_id}-reject", classes="act-reject")
            yield Button("Stop task", id=f"act-{request.interaction_id}-stop", classes="act-stop", variant="error")
            yield Button("Enable broader", id=f"act-{request.interaction_id}-enable", classes="act-enable")
        elif request.kind is InteractionKind.PLAN_APPROVAL:
            yield Button("Approve and run", id=f"act-{request.interaction_id}-approve", classes="act-approve", variant="primary")
            yield Button("Edit plan", id=f"act-{request.interaction_id}-edit", classes="act-edit")
            yield Button("Cancel", id=f"act-{request.interaction_id}-stop", classes="act-stop", variant="error")
        elif request.kind is InteractionKind.TEXT_INPUT:
            yield Input(id=f"act-{request.interaction_id}-input", placeholder="Type a response…")
            yield Button("Submit", id=f"act-{request.interaction_id}-submit", classes="act-submit", variant="primary")


class InspectorPanel(VerticalScroll):
    def __init__(self, app, *, id=None) -> None:
        super().__init__(id=id)
        self._app = app
        self.store = app.store
        self.kernel = app.kernel
        self._tick: Timer | None = None

    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("Context", id="context"):
                yield Static("Context — later gate.", id="context-stub")
            with TabPane("Activity", id="activity"):
                pass
            with TabPane("Changes", id="changes"):
                pass

    def on_mount(self) -> None:
        self.store.subscribe(self._on_store)
        self._tick = self.set_interval(1.0, self._render_panes)
        self._render_panes()

    def on_unmount(self) -> None:
        self.store.unsubscribe(self._on_store)
        if self._tick is not None:
            self._tick.stop()

    def _on_store(self, state) -> None:
        self._render_activity()
        self._render_changes()

    def _render_panes(self) -> None:
        self._render_activity()
        self._render_changes()

    def _render_activity(self) -> None:
        pane = self.query_one_optional("#activity", TabPane)
        if pane is None or not pane.is_attached:
            # Mid-shutdown or not yet mounted: skip; the next tick/store event re-renders.
            return
        pane.remove_children()
        pending = self.store.state.pending_interactions
        if not pending:
            pane.mount(Static("No pending interactions.", classes="act-empty"))
            return
        for request in pending:
            pane.mount(ActivityCard(request))

    def _render_changes(self) -> None:
        pane = self.query_one_optional("#changes", TabPane)
        if pane is None or not pane.is_attached:
            return
        pane.remove_children()
        state = self.store.state
        entries = [event for event in state.events if event.event_type is EventType.WORKSPACE_CHANGED]
        if not entries:
            pane.mount(Static("No workspace changes.", classes="changes-empty"))
        else:
            # Newest first: the event log is sequence-ordered oldest → newest.
            for event in reversed(entries):
                payload = event.payload
                if isinstance(payload, ChangeEvent):
                    summary = payload.summary or "Workspace changed."
                    pane.mount(Static(f"r{payload.revision} — {summary}", classes="changes-row"))
        pane.mount(Static(f"current revision: {state.workspace_revision}", classes="changes-footer"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("act-"):
            return
        interaction_id, action_name = button_id[len("act-"):].rsplit("-", 1)
        request = self._pending_interaction(interaction_id)
        if request is None:
            return
        if action_name == "edit":
            self.run_worker(self._edit_plan(request))
            return
        action = {
            "approve": InteractionAction.APPROVE_ONCE,
            "reject": InteractionAction.REJECT,
            "stop": InteractionAction.STOP,
            "enable": InteractionAction.ENABLE_AUTO,
            "submit": InteractionAction.SUBMIT_TEXT,
        }.get(action_name)
        if action is None:
            return
        text = self.query_one(f"#act-{interaction_id}-input", Input).value if action is InteractionAction.SUBMIT_TEXT else ""
        self.run_worker(self._respond(request, action, text))

    async def _edit_plan(self, request: InteractionRequest) -> None:
        text = await self._app.push_screen_wait(PlanEditModal())
        if text:
            await self._respond(request, InteractionAction.SUBMIT_TEXT, text)

    async def _respond(self, request: InteractionRequest, action: InteractionAction, text: str = "") -> None:
        await self.kernel.interactions.respond(
            InteractionResponse(request.interaction_id, request.turn_id, action, text)
        )

    def _pending_interaction(self, interaction_id: str) -> InteractionRequest | None:
        pending: tuple[InteractionRequest, ...] = self.store.state.pending_interactions
        for request in pending:
            if str(request.interaction_id) == interaction_id:
                return request
        return None
