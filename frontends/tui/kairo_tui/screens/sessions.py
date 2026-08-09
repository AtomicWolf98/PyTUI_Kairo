"""Sessions page: list/search/switch/rename/delete/export + clear/undo/compress.

Store-driven: sessions, running turns and the active session all come from
the store, refreshed from the kernel on mount and after every create/rename/
delete (``refresh_sessions``). Switching a session is a pure store dispatch —
no kernel call, so background turns of other sessions keep running
(tui_plan: switching never cancels). Export is a TUI-side file write under
``<workspace>/kairo_exports/`` (the kernel only returns the payload string).
"""

from __future__ import annotations

from pathlib import Path

from kairo_kernel.contracts.identifiers import SessionId
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, Static

from kairo_tui.page import refresh_sessions
from kairo_tui.sessions_model import session_rows
from kairo_tui.store import AppState, AppStore, SessionAction


class SessionTextModal(ModalScreen[str]):
    """One-input modal for rename and compress-summary inputs (PlanEditModal pattern)."""

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="session-text-modal"):
            yield Static(self._title, id="session-text-title")
            yield Input(id="session-text-input", placeholder=self._placeholder)
            yield Button("Submit", id="session-text-submit", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "session-text-submit":
            self.dismiss(self.query_one("#session-text-input", Input).value)


class SessionsScreen(Container):
    """Sessions list with actions; every row is a Button (id ``ses-{session_id}``)."""

    DEFAULT_CSS = """
    SessionsScreen {
        height: 1fr;
    }
    #sessions-list {
        height: 1fr;
    }
    #sessions-actions {
        height: auto;
    }
    /* Buttons default to min-width 16; seven of them (112 cols) overflow the
       ~80-col page area and clip the trailing actions off-screen. */
    #sessions-actions Button {
        min-width: 0;
    }
    """

    def __init__(self, app) -> None:
        super().__init__(id="sessions-screen")
        # Widget.app is a read-only Textual property, so the host app reference
        # lives at _app (same convention as SetupScreen and ChatScreen).
        self._app = app
        self.kernel = app.kernel
        self.store: AppStore = app.store
        self._filter_text = ""
        self._list_signature: tuple[tuple[str, ...], ...] | None = None
        self._list_dirty = False
        self._list_building = False
        self._flush_timer: Timer

    def compose(self) -> ComposeResult:
        yield Static("[b]Sessions[/b]", id="sessions-title")
        yield Input(id="sessions-search", placeholder="Search sessions…")
        with Horizontal(id="sessions-actions"):
            yield Button("New", id="sessions-new", variant="primary")
            yield Button("Rename", id="sessions-rename")
            yield Button("Delete", id="sessions-delete", variant="error")
            yield Button("Export", id="sessions-export")
            yield Button("Clear", id="sessions-clear")
            yield Button("Undo", id="sessions-undo")
            yield Button("Compress", id="sessions-compress")
        yield VerticalScroll(id="sessions-list")

    def on_mount(self) -> None:
        self._flush_timer = self.set_interval(1 / 30, self._flush)
        self.store.subscribe(self._on_store)
        self._render_controls(self.store.state)
        self._list_signature = None
        self._list_dirty = True
        self.run_worker(self._refresh())

    def on_unmount(self) -> None:
        self.store.unsubscribe(self._on_store)
        self._flush_timer.stop()

    async def _refresh(self) -> None:
        await refresh_sessions(self)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sessions-search":
            self._filter_text = event.value
            # Force a rebuild on the next flush; the store is untouched.
            self._list_signature = None
            self._list_dirty = True

    def _on_store(self, state: AppState) -> None:
        self._render_controls(state)
        signature = self._rows_signature(state)
        if signature != self._list_signature:
            self._list_signature = signature
            self._list_dirty = True

    def _rows_signature(self, state: AppState) -> tuple[tuple[str, ...], ...]:
        return tuple(
            (row.session_id, row.name, str(row.message_count), row.updated_at, str(row.running), str(row.active))
            for row in session_rows(state, self._filter_text)
        )

    def _render_controls(self, state: AppState) -> None:
        """Session actions need an active session; New is always enabled."""
        enabled = state.active_session_id is not None
        for button_id in (
            "sessions-rename", "sessions-delete", "sessions-export",
            "sessions-clear", "sessions-undo", "sessions-compress",
        ):
            button = self.query_one_optional(f"#{button_id}", Button)
            if button is not None:
                button.disabled = not enabled

    def _flush(self) -> None:
        if self._list_dirty:
            self.run_worker(self._rebuild_list())

    async def _rebuild_list(self) -> None:
        """(Re)mount the row buttons; serialized like the chat session chips."""
        if self._list_building:
            return
        self._list_building = True
        try:
            while self._list_dirty:
                self._list_dirty = False
                rows = session_rows(self.store.state, self._filter_text)
                container = self.query_one("#sessions-list", VerticalScroll)
                await container.remove_children()
                if not rows:
                    await container.mount(Static("No sessions.", id="sessions-empty"))
                    continue
                for row in rows:
                    label = self._row_label(row)
                    await container.mount(Button(
                        label,
                        id=f"ses-{row.session_id}",
                        variant="primary" if row.active else "default",
                    ))
        finally:
            self._list_building = False

    @staticmethod
    def _row_label(row) -> str:
        running = "● " if row.running else ""
        return f"{running}{row.name} — {row.message_count} messages — {row.updated_at}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "sessions-new":
            self.run_worker(self._new_session())
        elif button_id == "sessions-rename":
            self.run_worker(self._rename_session())
        elif button_id == "sessions-delete":
            self.run_worker(self._delete_session())
        elif button_id == "sessions-export":
            self.run_worker(self._export_session())
        elif button_id == "sessions-clear":
            self.run_worker(self._clear_conversation())
        elif button_id == "sessions-undo":
            self.run_worker(self._undo_turn())
        elif button_id == "sessions-compress":
            self.run_worker(self._compress_conversation())
        elif button_id.startswith("ses-"):
            # Session switch is purely a store dispatch: no kernel call, so
            # background turns of other sessions keep running.
            self.store.dispatch(SessionAction(button_id.removeprefix("ses-")))

    def _active_session_id(self) -> str | None:
        return self.store.state.active_session_id

    async def _new_session(self) -> None:
        created = await self.kernel.sessions.create("Chat")
        if created.ok and created.value is not None:
            self.store.dispatch(SessionAction(str(created.value.session_id)))
            await refresh_sessions(self)

    async def _rename_session(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        text = await self._app.push_screen_wait(SessionTextModal("Rename session", placeholder="New name…"))
        if not text or not text.strip():
            return
        result = await self.kernel.sessions.rename(SessionId(session_id), text.strip())
        if result.ok:
            await refresh_sessions(self)
        else:
            self._app.notify(f"Rename failed: {result.error.message}")

    async def _delete_session(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        result = await self.kernel.sessions.delete(SessionId(session_id))
        if not result.ok:
            self._app.notify(f"Delete failed: {result.error.message}")
            return
        if not result.value:
            return  # nothing was deleted (already gone); nothing to refresh
        await refresh_sessions(self)
        if session_id == self.store.state.active_session_id:
            # Activate the first remaining session (newest-updated first), or none.
            first = self.store.state.sessions[0] if self.store.state.sessions else None
            self.store.dispatch(SessionAction(str(first.session_id) if first is not None else None))

    async def _export_session(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        exported = await self.kernel.sessions.export(SessionId(session_id), format="json")
        if not exported.ok:
            self._app.notify(f"Export failed: {exported.error.message}")
            return
        if exported.value is None:
            return
        directory = Path(self.store.state.workspace_root) / "kairo_exports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"session-{session_id}.json"
        path.write_text(exported.value, encoding="utf-8")
        self._app.notify(str(path))

    async def _clear_conversation(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        result = await self.kernel.conversations.clear(SessionId(session_id))
        if not result.ok:
            self._app.notify(f"Clear failed: {result.error.message}")

    async def _undo_turn(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        result = await self.kernel.conversations.undo(SessionId(session_id))
        if not result.ok:
            self._app.notify(f"Undo failed: {result.error.message}")

    async def _compress_conversation(self) -> None:
        session_id = self._active_session_id()
        if session_id is None:
            return
        text = await self._app.push_screen_wait(
            SessionTextModal("Compress conversation", placeholder="Summary of older context…")
        )
        if not text or not text.strip():
            return
        result = await self.kernel.conversations.compress(SessionId(session_id), text.strip())
        if not result.ok:
            self._app.notify(f"Compress failed: {result.error.message}")
