"""Memory page: namespace/tag/text search, view, create/edit/delete.

Search is button-driven: ``kernel.memory.search(build_query(...))`` renders the
result rows (buttons ``mem-{memory_id}``); an empty namespace surfaces the
kernel's INVALID_ARGUMENT inline in the status line, never silently swallowed.
Clicking a row views the full entry via ``kernel.memory.get`` in the detail
pane. New opens an empty ``MemoryFormModal`` → ``new_entry`` → ``put``; Edit
opens the modal prefilled (namespace+key are disabled — immutable once created)
and re-``put`` with the same ``memory_id``; Delete asks for confirmation in
``MemoryDeleteModal`` before ``kernel.memory.delete``. Every mutation re-runs
the current search so results refresh without an extra click.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kairo_kernel.contracts.content import TextBlock
from kairo_kernel.contracts.identifiers import MemoryId
from kairo_kernel.contracts.support import MemoryEntry
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from kairo_tui.memory_model import build_query, memory_rows, new_entry


@dataclass(frozen=True)
class MemoryFormData:
    namespace: str
    key: str
    text: str
    tags: tuple[str, ...]


class MemoryFormModal(ModalScreen[MemoryFormData | None]):
    """Four-field entry form; namespace+key are disabled once created (immutable)."""

    def __init__(self, *, namespace: str = "", key: str = "", text: str = "", tags: tuple[str, ...] = (),
                 editing: bool = False) -> None:
        super().__init__()
        self._namespace = namespace
        self._key = key
        self._text = text
        self._tags = ", ".join(tags)
        self._editing = editing

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-form-modal"):
            yield Static("New memory entry" if not self._editing else "Edit memory entry", id="memory-form-title")
            yield Input(self._namespace, id="memory-form-namespace", placeholder="Namespace", disabled=self._editing)
            yield Input(self._key, id="memory-form-key", placeholder="Key", disabled=self._editing)
            yield Input(self._text, id="memory-form-text", placeholder="Content")
            yield Input(self._tags, id="memory-form-tags", placeholder="Tags (comma/space separated)")
            with Horizontal(id="memory-form-actions"):
                yield Button("Save", id="memory-form-save", variant="primary")
                yield Button("Cancel", id="memory-form-cancel")

    def _submit(self) -> None:
        tags = tuple(t for t in self.query_one("#memory-form-tags", Input).value.replace(",", " ").split() if t)
        self.dismiss(MemoryFormData(
            self.query_one("#memory-form-namespace", Input).value.strip(),
            self.query_one("#memory-form-key", Input).value.strip(),
            self.query_one("#memory-form-text", Input).value,
            tags,
        ))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "memory-form-save":
            self._submit()
        elif event.button.id == "memory-form-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "memory-form-text":
            self._submit()


class MemoryDeleteModal(ModalScreen[str]):
    """Destructive-action confirmation (ExitWithTurnsModal pattern)."""

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-delete-modal"):
            yield Static("Delete this memory entry?", id="memory-delete-title")
            yield Button("Delete", id="memory-delete-confirm", variant="error")
            yield Button("Cancel", id="memory-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "memory-delete-confirm":
            self.dismiss("confirm")
        elif event.button.id == "memory-delete-cancel":
            self.dismiss("cancel")


class MemoryScreen(Container):
    """Memory page: search bar + result list + detail pane with create/edit/delete."""

    DEFAULT_CSS = """
    MemoryScreen { height: 1fr; }
    #memory-main { height: 1fr; }
    #memory-results { width: 1fr; height: 100%; }
    #memory-detail { width: 1fr; height: 100%; }
    /* The page area is only ~80 cols at the full breakpoint (nav 22 +
       inspector 38) and Input/Button default to filling the container, so
       explicit widths are required or the search row overflows. */
    #memory-search Input { width: 16; }
    #memory-search Button { width: 12; min-width: 0; }
    #memory-detail-actions Button { min-width: 0; }
    """

    def __init__(self, app) -> None:
        super().__init__(id="memory-screen")
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
        self._detail_id: str | None = None
        self._detail_entry: MemoryEntry | None = None

    def compose(self) -> ComposeResult:
        yield Static("[b]Memory[/b]", id="memory-title")
        with Horizontal(id="memory-search"):
            yield Input(placeholder="Namespace", id="memory-namespace")
            yield Input(placeholder="Text", id="memory-text")
            yield Input(placeholder="Tags", id="memory-tags")
            yield Button("Search", id="memory-search-button", variant="primary")
            yield Button("New", id="memory-new")
        with Horizontal(id="memory-main"):
            yield VerticalScroll(id="memory-results")
            with VerticalScroll(id="memory-detail"):
                yield Static("No entry selected.", id="memory-detail-empty")
                yield Static("", id="memory-detail-namespace")
                yield Static("", id="memory-detail-key")
                yield Static("", id="memory-detail-tags")
                yield Static("", id="memory-detail-content", markup=False)
                with Horizontal(id="memory-detail-actions"):
                    yield Button("Edit", id="memory-edit", disabled=True)
                    yield Button("Delete", id="memory-delete", variant="error", disabled=True)
        yield Static("", id="memory-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "memory-search-button":
            self.run_worker(self._search())
        elif button_id == "memory-new":
            self.run_worker(self._new())
        elif button_id == "memory-edit":
            self.run_worker(self._edit())
        elif button_id == "memory-delete":
            self.run_worker(self._delete())
        elif button_id.startswith("mem-"):
            self.run_worker(self._view(button_id.removeprefix("mem-")))

    async def _search(self) -> None:
        query = build_query(
            self.query_one("#memory-namespace", Input).value,
            self.query_one("#memory-text", Input).value,
            self.query_one("#memory-tags", Input).value,
        )
        result = await self.kernel.memory.search(query)
        if not result.ok:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Memory search failed.")
            return
        if not self.is_mounted:
            return
        self._notice("")
        await self._render_rows(result.value)

    async def _render_rows(self, entries: tuple[MemoryEntry, ...]) -> None:
        container = self.query_one("#memory-results", VerticalScroll)
        await container.remove_children()
        rows = memory_rows(entries)
        if not rows:
            await container.mount(Static("No results. Enter a namespace and press Search.", id="memory-empty"))
            return
        for row in rows:
            await container.mount(Button(f"{row.namespace}:{row.key} — {row.preview}", id=f"mem-{row.memory_id}"))

    async def _view(self, memory_id: str) -> None:
        result = await self.kernel.memory.get(MemoryId(memory_id))
        if not result.ok or result.value is None:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Memory entry unavailable.")
            return
        if not self.is_mounted:
            return
        self._detail_id = memory_id
        self._detail_entry = result.value
        self._render_detail(result.value)

    def _render_detail(self, entry: MemoryEntry | None) -> None:
        if entry is None:
            self.query_one("#memory-detail-empty", Static).display = True
            self.query_one("#memory-detail-namespace", Static).update("")
            self.query_one("#memory-detail-key", Static).update("")
            self.query_one("#memory-detail-tags", Static).update("")
            self.query_one("#memory-detail-content", Static).update("")
            self.query_one("#memory-edit", Button).disabled = True
            self.query_one("#memory-delete", Button).disabled = True
            return
        self.query_one("#memory-detail-empty", Static).display = False
        self.query_one("#memory-detail-namespace", Static).update(f"Namespace: {entry.namespace}")
        self.query_one("#memory-detail-key", Static).update(f"Key: {entry.key}")
        self.query_one("#memory-detail-tags", Static).update(f"Tags: {', '.join(entry.tags) or '—'}")
        self.query_one("#memory-detail-content", Static).update(
            "".join(b.text for b in entry.content if isinstance(b, TextBlock))
        )
        self.query_one("#memory-edit", Button).disabled = False
        self.query_one("#memory-delete", Button).disabled = False

    async def _new(self) -> None:
        data = await self._app.push_screen_wait(MemoryFormModal())
        if data is None:
            return
        entry = new_entry(data.namespace, data.key, data.text, data.tags)
        result = await self.kernel.memory.put(entry)
        if not result.ok:
            self._notice(result.error.message if result.error else "Memory save failed.")
            return
        if self.is_mounted:
            await self._search()

    async def _edit(self) -> None:
        entry = self._detail_entry
        if entry is None:
            return
        text = "".join(b.text for b in entry.content if isinstance(b, TextBlock))
        data = await self._app.push_screen_wait(MemoryFormModal(
            namespace=entry.namespace,
            key=entry.key,
            text=text,
            tags=entry.tags,
            editing=True,
        ))
        if data is None:
            return
        now = datetime.now(timezone.utc)
        updated = MemoryEntry(entry.memory_id, entry.namespace, entry.key,
                              (TextBlock(data.text),), entry.created_at, now, data.tags)
        result = await self.kernel.memory.put(updated)
        if not result.ok:
            self._notice(result.error.message if result.error else "Memory save failed.")
            return
        self._detail_entry = updated
        if self.is_mounted:
            await self._search()
            await self._view(str(entry.memory_id))

    async def _delete(self) -> None:
        memory_id = self._detail_id
        if memory_id is None:
            return
        choice = await self._app.push_screen_wait(MemoryDeleteModal())
        if choice != "confirm":
            return
        result = await self.kernel.memory.delete(MemoryId(memory_id))
        if not result.ok:
            self._notice(result.error.message if result.error else "Memory delete failed.")
            return
        self._detail_id = None
        self._detail_entry = None
        if self.is_mounted:
            self._render_detail(None)
            await self._search()

    def _notice(self, message: str) -> None:
        status = self.query_one_optional("#memory-status", Static)
        if status is not None:
            status.update(message)
