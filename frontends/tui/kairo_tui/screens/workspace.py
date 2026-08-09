"""Workspace page: lazy directory tree, preview, git changed files + diff,
bookmarks, workspace switch; every read result is stale-dropped (tui_plan.md).

All workspace reads carry ``root`` + ``revision``; a result is dropped unless
both match ``store.state.workspace_root``/``workspace_revision`` (``is_stale``
in workspace_model.py). Mutations pass ``expected_revision``; BUSY/CONFLICT/
invalid errors are surfaced in the status line, never silently swallowed. On a
successful move the store is updated synchronously (``WorkspaceAction``) and
the recent workspace is recorded (``record_recent_workspace``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static, Tree

from kairo_tui.page import record_recent_workspace
from kairo_tui.store import AppStore, WorkspaceAction
from kairo_tui.structs import (
    ChangedFilesLike,
    WorkspaceDiffLike,
    WorkspacePreviewLike,
    WorkspaceStateLike,
    WorkspaceTreeLike,
)
from kairo_tui.workspace_model import change_button_id, changed_rows, is_stale


@dataclass(frozen=True)
class _Bookmark:
    """Structural stand-in for the kernel WorkspaceBookmark (AST-boundary).

    The real class lives in a forbidden kernel module; the facade only reads
    ``.name``/``.path``, so a local attribute-compatible value is passed.
    """

    name: str
    path: str


class WorkspaceTreeWidget(Tree):
    """Lazy directory tree: children are fetched on expand, never up-front."""

    def __init__(self, kernel, store) -> None:
        super().__init__("workspace", id="workspace-tree")
        self._kernel, self._store = kernel, store

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        path = node.data or "."
        # Directory nodes are seeded with a "…" placeholder leaf (data None) so
        # the expand arrow shows; only real children (data set) mean the node
        # was already loaded. (Plan Task 3 fix: the verbatim guard
        # `if node.children: return` would never replace the placeholder.)
        if any(child.data is not None for child in node.children):
            return
        result = await self._kernel.workspace.tree(str(path))
        if not result.ok or result.value is None:
            return
        tree = cast(WorkspaceTreeLike, result.value)
        if is_stale(tree.root, tree.revision, self._store.state):
            return  # stale response dropped (tui_plan.md)
        node.remove_children()  # replace the placeholder
        for entry in tree.entries:
            label = f"{entry.name}/" if entry.is_directory else entry.name
            child = node.add(label, data=entry.relative_path)
            if entry.is_directory:
                child.add_leaf("…")  # placeholder so the arrow shows; replaced on expand
        if tree.truncated:
            node.add_leaf("… truncated …")


class WorkspaceScreen(Container):
    """Workspace page: tree + preview + changed files + diff + bookmarks + switch."""

    DEFAULT_CSS = """
    WorkspaceScreen { height: 1fr; }
    #workspace-main { height: 1fr; }
    #workspace-tree { width: 1fr; height: 100%; }
    #workspace-pane { width: 1fr; height: 100%; }
    #workspace-changes { height: 6; }
    #workspace-preview { height: 6; }
    #workspace-diff { height: 6; }
    #workspace-bookmark-list { height: 4; }
    /* The page area is only ~80 cols at the full breakpoint (nav 22 +
       inspector 38) and Input/Static default to filling the container, so
       explicit widths are required or rows overflow under the inspector and
       buttons become unclickable. */
    #workspace-bookmark-name { width: 24; }
    #workspace-bookmark-path { width: 1fr; }
    #workspace-switch-target { width: 1fr; }
    #workspace-bookmarks Button, #workspace-switch Button { min-width: 0; }
    #workspace-bookmark-list Static { width: 1fr; }
    #workspace-bookmark-list Button { width: 16; min-width: 0; }
    """

    def __init__(self, app) -> None:
        super().__init__(id="workspace-screen")
        self._app = app
        self.kernel = app.kernel
        self.store: AppStore = app.store
        self._change_paths: dict[str, str] = {}   # sanitized id → real relative path
        self._bookmark_names: dict[str, str] = {}  # sanitized id → real bookmark name

    def compose(self) -> ComposeResult:
        yield Static("[b]Workspace[/b]", id="workspace-title")
        with Horizontal(id="workspace-main"):
            yield WorkspaceTreeWidget(self.kernel, self.store)
            # Changed files first so its rows stay inside the pane viewport
            # (the pane is short at the full breakpoint); preview/diff follow
            # below and scroll into view.
            with VerticalScroll(id="workspace-pane"):
                yield Static("[b]Changed files[/b]", id="workspace-changes-label")
                yield VerticalScroll(id="workspace-changes")
                yield Static("[b]Preview[/b]", id="workspace-preview-label")
                yield Static("", id="workspace-preview", markup=False)
                yield Static("[b]Diff[/b]", id="workspace-diff-label")
                yield Static("", id="workspace-diff", markup=False)
        with Horizontal(id="workspace-bookmarks"):
            yield Input(id="workspace-bookmark-name", placeholder="Name")
            yield Input(id="workspace-bookmark-path", placeholder="Path")
            yield Button("Save", id="workspace-bookmark-save", variant="primary")
        yield VerticalScroll(id="workspace-bookmark-list")
        with Horizontal(id="workspace-switch"):
            yield Input(id="workspace-switch-target", placeholder="Target workspace")
            yield Button("Move", id="workspace-move", variant="primary")
        yield Static("", id="workspace-status")

    def on_mount(self) -> None:
        self.run_worker(self._load_all())

    async def _load_all(self) -> None:
        await self._fetch_tree(".")
        await self._load_bookmarks()
        await self._fetch_changes()

    async def _fetch_tree(self, relative_path: str) -> None:
        result = await self.kernel.workspace.tree(relative_path)
        if not result.ok or result.value is None:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Tree unavailable.")
            return
        tree = cast(WorkspaceTreeLike, result.value)
        if is_stale(tree.root, tree.revision, self.store.state):
            return  # stale response dropped (tui_plan.md)
        if not self.is_mounted:
            return
        widget = self.query_one("#workspace-tree", WorkspaceTreeWidget)
        widget.root.remove_children()
        for entry in tree.entries:
            label = f"{entry.name}/" if entry.is_directory else entry.name
            child = widget.root.add(label, data=entry.relative_path)
            if entry.is_directory:
                child.add_leaf("…")
        if tree.truncated:
            widget.root.add_leaf("… truncated …")

    async def _load_bookmarks(self) -> None:
        snapshot = cast(WorkspaceStateLike, await self.kernel.workspace.snapshot())
        if is_stale(snapshot.root, snapshot.revision, self.store.state):
            return  # stale response dropped (tui_plan.md)
        if not self.is_mounted:
            return
        await self._render_bookmarks(snapshot.bookmarks)

    async def _fetch_changes(self) -> None:
        result = await self.kernel.workspace.changed_files()
        if not result.ok or result.value is None:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Changed files unavailable.")
            return
        changed = cast(ChangedFilesLike, result.value)
        if is_stale(changed.root, changed.revision, self.store.state):
            return  # stale response dropped (tui_plan.md)
        if not self.is_mounted:
            return
        container = self.query_one("#workspace-changes", VerticalScroll)
        await container.remove_children()
        if not changed.is_git_repository:
            await container.mount(Static("Not a git repository.", id="workspace-changes-empty"))
            return
        rows = changed_rows(changed)
        if not rows:
            await container.mount(Static("No changes.", id="workspace-changes-empty"))
            return
        self._change_paths = {}
        for row in rows:
            key = change_button_id(row.relative_path)
            self._change_paths[key] = row.relative_path
            await container.mount(Button(row.label, id=f"chg-{key}"))

    async def _preview(self, relative_path: str) -> None:
        result = await self.kernel.workspace.preview(relative_path)
        if not result.ok or result.value is None:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Preview unavailable.")
            return
        preview = cast(WorkspacePreviewLike, result.value)
        if is_stale(preview.root, preview.revision, self.store.state):
            return  # stale response dropped (tui_plan.md)
        if not self.is_mounted:
            return
        if preview.is_directory:
            children = "\n".join(f"  {name}" for name in preview.children) or "  (empty)"
            text = f"Directory: {preview.relative_path}\n{children}"
        else:
            text = preview.text
        if preview.truncated:
            text += "\n… truncated …"
        self.query_one("#workspace-preview", Static).update(text)

    async def _fetch_diff(self, relative_path: str) -> None:
        result = await self.kernel.workspace.diff(relative_path)
        if not result.ok or result.value is None:
            if self.is_mounted:
                self._notice(result.error.message if result.error else "Diff unavailable.")
            return
        diff = cast(WorkspaceDiffLike, result.value)
        if is_stale(diff.root, diff.revision, self.store.state):
            return  # stale response dropped (tui_plan.md)
        if not self.is_mounted:
            return
        if diff.status == "untracked":
            text = "untracked"
        else:
            text = diff.unified_diff
            if diff.truncated:
                text += "\n… truncated …"
        self.query_one("#workspace-diff", Static).update(text)

    async def _save_bookmark(self) -> None:
        name_input = self.query_one("#workspace-bookmark-name", Input)
        path_input = self.query_one("#workspace-bookmark-path", Input)
        name, path = name_input.value.strip(), path_input.value.strip()
        if not name or not path:
            self._notice("Bookmark name and path are required.")
            return
        result = await self.kernel.workspace.save_bookmark(_Bookmark(name, path), self.store.state.workspace_revision)
        if not result.ok:
            self._notice(result.error.message if result.error else "Bookmark save failed.")
            return
        if result.value is not None:
            self.store.dispatch(WorkspaceAction(result.value.root, result.value.revision))
            if self.is_mounted:
                await self._render_bookmarks(result.value.bookmarks)
                name_input.value = ""
                path_input.value = ""

    async def _remove_bookmark(self, name: str) -> None:
        result = await self.kernel.workspace.remove_bookmark(name, self.store.state.workspace_revision)
        if not result.ok:
            self._notice(result.error.message if result.error else "Bookmark remove failed.")
            return
        if result.value is not None:
            self.store.dispatch(WorkspaceAction(result.value.root, result.value.revision))
            if self.is_mounted:
                await self._render_bookmarks(result.value.bookmarks)

    async def _move(self) -> None:
        target_input = self.query_one("#workspace-switch-target", Input)
        target = target_input.value.strip()
        if not target:
            self._notice("Workspace target is required.")
            return
        result = await self.kernel.workspace.move(target, self.store.state.workspace_revision)
        if not result.ok:
            # KERNEL_BUSY / CONFLICT / invalid messages are surfaced here, never
            # silently dropped.
            self._notice(result.error.message if result.error else "Workspace move failed.")
            return
        if result.value is not None:
            self.store.dispatch(WorkspaceAction(result.value.root, result.value.revision))
            record_recent_workspace(self._app, result.value.root)
            target_input.value = ""
            await self._reload()

    async def _reload(self) -> None:
        await self._fetch_tree(".")
        await self._load_bookmarks()
        await self._fetch_changes()

    async def _render_bookmarks(self, bookmarks) -> None:
        container = self.query_one("#workspace-bookmark-list", VerticalScroll)
        await container.remove_children()
        if not bookmarks:
            await container.mount(Static("No bookmarks.", id="workspace-bookmarks-empty"))
            return
        self._bookmark_names = {}
        for bookmark in bookmarks:
            key = change_button_id(bookmark.name)
            self._bookmark_names[key] = bookmark.name
            row = Horizontal(id=f"bm-row-{key}")
            await container.mount(row)
            row.mount(Static(f"{bookmark.name} → {bookmark.path}", id=f"bm-{key}"))
            row.mount(Button("Remove", id=f"bm-remove-{key}"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "workspace-bookmark-save":
            self.run_worker(self._save_bookmark())
        elif button_id == "workspace-move":
            self.run_worker(self._move())
        elif button_id.startswith("chg-"):
            path = self._change_paths.get(button_id.removeprefix("chg-"), "")
            if path:
                self.run_worker(self._fetch_diff(path))
        elif button_id.startswith("bm-remove-"):
            name = self._bookmark_names.get(button_id.removeprefix("bm-remove-"), "")
            if name:
                self.run_worker(self._remove_bookmark(name))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        path = node.data
        if path is not None:
            self.run_worker(self._preview(str(path)))

    def _notice(self, message: str) -> None:
        status = self.query_one_optional("#workspace-status", Static)
        if status is not None:
            status.update(message)
