from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label, ListItem, ListView, Markdown, ProgressBar, Static, TextArea, Tree

from agent.ui.mascot import KaiMascot
from agent.workspace import WorkspaceSnapshot


class Composer(TextArea):
    MIN_HEIGHT = 3
    MAX_VISIBLE_LINES = 8
    FRAME_HEIGHT = 2
    MAX_HEIGHT = MAX_VISIBLE_LINES + FRAME_HEIGHT
    NEWLINE_KEYS = {
        "shift+enter",
        "ctrl+enter",
        "ctrl+shift+enter",
        "ctrl+j",
        "newline",
    }

    class Submitted(Message):
        def __init__(self, value: str):
            super().__init__()
            self.value = value

    class CompleteRequested(Message):
        pass

    class HistoryRequested(Message):
        def __init__(self, direction: int):
            super().__init__()
            self.direction = direction

    class PaletteNavigate(Message):
        def __init__(self, direction: int):
            super().__init__()
            self.direction = direction

    class PaletteAccepted(Message):
        pass

    class PaletteDismissed(Message):
        pass

    palette_open = False

    def on_key(self, event: events.Key):
        if self.palette_open and event.key in ("up", "down"):
            event.prevent_default()
            event.stop()
            self.post_message(self.PaletteNavigate(-1 if event.key == "up" else 1))
        elif self.palette_open and event.key == "escape":
            event.prevent_default()
            event.stop()
            self.post_message(self.PaletteDismissed())
        elif self.palette_open and event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.PaletteAccepted())
        elif any(alias in self.NEWLINE_KEYS for alias in event.aliases):
            # Insert a newline instead of submitting.
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "enter":
            raw_value = self.text
            if raw_value.strip():
                event.prevent_default()
                event.stop()
                self.post_message(self.Submitted(raw_value.rstrip("\n")))
        elif event.key == "tab":
            event.prevent_default()
            event.stop()
            self.post_message(self.CompleteRequested())
        elif event.key == "ctrl+up":
            event.prevent_default()
            event.stop()
            self.post_message(self.HistoryRequested(-1))
        elif event.key == "ctrl+down":
            event.prevent_default()
            event.stop()
            self.post_message(self.HistoryRequested(1))

    def update_height(self) -> None:
        """Grow or shrink the composer to fit wrapped visual lines."""
        visual_lines = max(1, self.document.line_count, self.virtual_size.height)
        target = min(
            self.MAX_HEIGHT,
            max(self.MIN_HEIGHT, visual_lines + self.FRAME_HEIGHT),
        )
        self.styles.height = target

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self:
            # TextArea updates its wrapped document during refresh. Measuring on
            # the next refresh includes soft-wrapped rows, not just explicit \n.
            self.call_after_refresh(self.update_height)


class CommandPalette(ListView):
    class Chosen(Message):
        def __init__(self, command: str):
            super().__init__()
            self.command = command

    def __init__(self, commands: Dict[str, str], **kwargs):
        self.commands = dict(commands)
        self.command_names = list(self.commands)
        self.matches: List[str] = []
        self.visible_indices: List[int] = []
        items = [
            ListItem(
                Label(Text.assemble(
                    (command.ljust(13), "bold #66d9ef"),
                    (description, "#a5adcb"),
                )),
                classes="command-option",
            )
            for command, description in self.commands.items()
        ]
        super().__init__(*items, initial_index=0, **kwargs)

    def set_matches(self, matches: Sequence[str], descriptions: Dict[str, str]):
        del descriptions  # Labels are stable; only visibility changes while filtering.
        self.matches = list(matches)
        match_set = set(self.matches)
        self.visible_indices = []
        for index, item in enumerate(self.children):
            visible = self.command_names[index] in match_set
            item.display = visible
            if visible:
                self.visible_indices.append(index)
        self.index = self.visible_indices[0] if self.visible_indices else None
        if self.index is not None:
            self.scroll_to_widget(self.children[self.index], animate=False, immediate=True, force=True)
        else:
            self.scroll_to(y=0, animate=False, force=True)
        self.set_class(bool(self.matches), "visible")

    def move_selection(self, direction: int):
        if not self.visible_indices:
            return
        try:
            current = self.visible_indices.index(self.index)
        except ValueError:
            current = 0
        self.index = self.visible_indices[(current + direction) % len(self.visible_indices)]
        self.scroll_to_widget(self.children[self.index], animate=False, immediate=True, force=True)

    @property
    def selected_command(self) -> str:
        if self.index is None or self.index not in self.visible_indices:
            return ""
        return self.command_names[self.index]

    def on_list_view_selected(self, event: ListView.Selected):
        if event.index in self.visible_indices:
            self.post_message(self.Chosen(self.command_names[event.index]))


class ThoughtView(Static):
    def on_click(self):
        self.toggle_class("collapsed-thought")


class ExpandableToolOutput(Static):
    def __init__(self, name: str, result: str):
        self.name_text = name
        self.full_result = result
        self.expanded = False
        super().__init__(self._render_content(), classes="message tool-message")

    def _render_content(self):
        body = self.full_result
        marker = "[collapse]"
        if not self.expanded and len(body) > 1600:
            body = body[:1600] + "\n... [click to expand] ..."
            marker = ""
        return Text(f"{self.name_text}\n{body}\n{marker}", style="#c6a0f6")

    def on_click(self):
        if len(self.full_result) <= 1600:
            return
        self.expanded = not self.expanded
        self.update(self._render_content())


class MessageBody(VerticalScroll):
    """Unified message renderer: Markdown for prose, scrollable for wide blocks."""

    DEFAULT_CSS = """
    MessageBody MarkdownTableContent > .cell,
    MessageBody MarkdownTableContent > .header {
        text-overflow: fold;
    }
    """

    def __init__(self, content: str = "", *, is_markdown: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._content = content
        self._is_markdown = is_markdown

    def compose(self):
        if self._is_markdown:
            yield Markdown(self._content)
        else:
            yield Static(self._content)

    def update_content(self, content: str) -> None:
        self._content = content
        if self._is_markdown:
            self.query_one(Markdown).update(content)
        else:
            self.query_one(Static).update(content)

    def append_content(self, chunk: str) -> None:
        self._content += chunk
        self.update_content(self._content)


class ConversationView(VerticalScroll):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._assistant_widget: Optional[MessageBody] = None
        self._assistant_text = ""
        self._thought_widget: Optional[ThoughtView] = None
        self._thought_text = ""

    async def add_user(self, text: str):
        widget = MessageBody(text, is_markdown=True, classes="message user-message")
        await self.mount(widget)
        self.scroll_end(animate=False)

    async def add_notice(self, content, classes: str = "notice-message"):
        if isinstance(content, str):
            widget = MessageBody(content, is_markdown=True, classes=f"message {classes}")
        else:
            widget = MessageBody(content, is_markdown=False, classes=f"message {classes}")
        await self.mount(widget)
        self.scroll_end(animate=False)

    async def add_tool_result(self, name: str, result: str):
        await self.mount(ExpandableToolOutput(name, result))
        self.scroll_end(animate=False)

    async def clear_messages(self):
        await self.remove_children()
        self._assistant_widget = None
        self._thought_widget = None
        self._assistant_text = ""
        self._thought_text = ""

    async def render_history(self, history):
        await self.clear_messages()
        for message in history:
            role = message.get("role")
            content = message.get("content", "") or ""
            if role == "system":
                if content.startswith("[Conversation Summary]"):
                    await self.add_notice(Text(content, style="#a5adcb"))
            elif role == "user":
                await self.add_user(content)
            elif role == "assistant":
                await self.add_notice(content or "_Tool request_", "assistant-message")
            elif role == "tool":
                await self.add_notice(Text(f"{message.get('name', 'tool')}\n{content}", style="#c6a0f6"), "tool-message")

    async def start_assistant(self):
        self._assistant_text = ""
        self._thought_text = ""
        self._assistant_widget = MessageBody("", is_markdown=True, classes="message assistant-message")
        self._thought_widget = ThoughtView("", classes="message thought-message hidden")
        await self.mount(self._thought_widget, self._assistant_widget)

    def append_content(self, chunk: str):
        self._assistant_text += chunk
        if self._assistant_widget:
            self._assistant_widget.update_content(self._assistant_text + " ▌")
        self.scroll_end(animate=False)

    def append_thought(self, chunk: str):
        self._thought_text += chunk
        if self._thought_widget:
            self._thought_widget.remove_class("hidden")
            preview = self._thought_text[-500:]
            self._thought_widget.update(Text("Thinking\n" + preview, style="italic #a5adcb"))

    def finish_assistant(self):
        if self._assistant_widget:
            self._assistant_widget.update_content(self._assistant_text or "_No response content._")
        if self._thought_widget:
            self._thought_widget.add_class("collapsed-thought")


class BrandHeader(Horizontal):
    def __init__(self, model: str, profile: str, cwd: str, *, reduced_motion: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.model = model
        self.profile = profile
        self.cwd = cwd
        self.reduced_motion = reduced_motion

    def compose(self):
        yield KaiMascot(id="header-kai", reduced_motion=self.reduced_motion)
        yield Static(
            Text.from_markup(
                f"[bold #f5f7fa]KAIRO[/bold #f5f7fa] [#7f849c]v0.2.7[/#7f849c]\n"
                f"[#a5adcb]{self.profile or self.model}[/#a5adcb]  [#6e738d]({self.model})[/#6e738d]\n"
                f"[#7f849c]{self.cwd}[/#7f849c]"
            ),
            id="brand-meta",
        )

    def update_meta(self, model: str, profile: str, cwd: str):
        self.model, self.profile, self.cwd = model, profile, cwd
        self.query_one("#brand-meta", Static).update(
            Text.from_markup(
                f"[bold #f5f7fa]KAIRO[/bold #f5f7fa] [#7f849c]v0.2.7[/#7f849c]\n"
                f"[#a5adcb]{profile or model}[/#a5adcb]  [#6e738d]({model})[/#6e738d]\n"
                f"[#7f849c]{cwd}[/#7f849c]"
            )
        )


class WorkspaceFileSelected(Message):
    def __init__(self, path: str):
        super().__init__()
        self.path = path


class WorkspaceTree(Tree[str]):
    def __init__(self, **kwargs):
        super().__init__("Workspace", id=kwargs.pop("id", None), **kwargs)
        self._tree_signature = ()

    def update_snapshot(self, snapshot: WorkspaceSnapshot):
        root_path = Path(snapshot.root).resolve()
        signature = (
            str(root_path),
            snapshot.files,
            tuple((change.path, change.status) for change in snapshot.changes),
        )
        if signature == self._tree_signature:
            return
        expanded = self._expanded_paths(self.root)
        self.clear()
        self.root.label = root_path.name or str(root_path)
        self.root.data = ""
        nodes = {"": self.root}
        change_map = {change.path: change for change in snapshot.changes}
        for file_path in snapshot.files:
            parts = Path(file_path).parts
            parent_key = ""
            for index, part in enumerate(parts):
                current_key = "/".join(parts[:index + 1])
                if current_key in nodes:
                    parent_key = current_key
                    continue
                parent = nodes[parent_key]
                if index == len(parts) - 1:
                    change = change_map.get(file_path)
                    marker = f"[{change.status}] " if change else ""
                    label = Text(marker + part, style="#f6c177" if change else "#cdd6f4")
                    nodes[current_key] = parent.add_leaf(label, data=file_path)
                else:
                    nodes[current_key] = parent.add(Text(part + "/", style="#66d9ef"), data=current_key)
                parent_key = current_key
        self.root.expand()
        for path in expanded:
            if path in nodes:
                nodes[path].expand()
        self._tree_signature = signature

    def _expanded_paths(self, node) -> set[str]:
        values = set()
        if node.is_expanded and node.data:
            values.add(str(node.data))
        for child in node.children:
            values.update(self._expanded_paths(child))
        return values

    def on_tree_node_selected(self, event: Tree.NodeSelected):
        path = str(event.node.data or "")
        if path and not event.node.allow_expand:
            self.post_message(WorkspaceFileSelected(path))


class ChangedFiles(ListView):
    def __init__(self, **kwargs):
        super().__init__(initial_index=None, **kwargs)
        self.paths: List[str] = []
        self._change_signature = ()

    async def update_snapshot(self, snapshot: WorkspaceSnapshot):
        selected = snapshot.selected_file
        signature = (
            str(Path(snapshot.root).resolve()),
            tuple(
                (change.path, change.status, change.session_touched, change.staged)
                for change in snapshot.changes
            ),
        )
        if signature == self._change_signature:
            if selected in self.paths:
                self.index = self.paths.index(selected)
            return
        await self.clear()
        self.paths = [change.path for change in snapshot.changes]
        if not snapshot.changes:
            await self.append(ListItem(Label("Clean working tree"), disabled=True))
            self.index = None
            self._change_signature = signature
            return
        items = []
        for change in snapshot.changes:
            prefix = "●" if change.session_touched else "·"
            stage = " staged" if change.staged else ""
            label = Text(f"{prefix} {change.status:<2} {change.path}{stage}")
            label.stylize("#8bd5ca" if change.session_touched else "#a5adcb")
            items.append(ListItem(Label(label)))
        await self.extend(items)
        self.index = self.paths.index(selected) if selected in self.paths else 0
        self._change_signature = signature

    def on_list_view_selected(self, event: ListView.Selected):
        if 0 <= event.index < len(self.paths):
            self.post_message(WorkspaceFileSelected(self.paths[event.index]))


class DiffViewer(ScrollableContainer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._diff_signature = None

    def compose(self):
        yield Static("Select a changed file to review.", id="diff-content")

    def update_snapshot(self, snapshot: WorkspaceSnapshot):
        signature = (
            str(Path(snapshot.root).resolve()),
            snapshot.selected_file, snapshot.diff, snapshot.diff_truncated, snapshot.error,
        )
        if signature == self._diff_signature:
            return
        title = snapshot.selected_file or "No file selected"
        content = snapshot.diff
        if snapshot.diff_truncated:
            title += "  [truncated]"
        if snapshot.error:
            content = f"{snapshot.error}\n\n{content}"
        self.query_one("#diff-content", Static).update(
            Syntax(content or "No textual diff.", "diff", theme="ansi_dark", word_wrap=False)
        )
        self.border_title = title
        self._diff_signature = signature


class WorkspacePanel(Vertical):
    def compose(self):
        yield Static("FILES", classes="workspace-heading")
        yield WorkspaceTree(id="workspace-tree")
        yield Static("CHANGES", classes="workspace-heading")
        yield ChangedFiles(id="changed-files")
        yield Static("REVIEW", classes="workspace-heading")
        yield DiffViewer(id="diff-viewer")
        yield Static("", id="workspace-note")

    async def update_snapshot(self, snapshot: WorkspaceSnapshot):
        self.query_one(WorkspaceTree).update_snapshot(snapshot)
        await self.query_one(ChangedFiles).update_snapshot(snapshot)
        self.query_one(DiffViewer).update_snapshot(snapshot)
        notes = []
        if snapshot.active_file:
            notes.append(f"Editing: {snapshot.active_file}")
        if snapshot.tree_truncated:
            notes.append("File list truncated")
        self.query_one("#workspace-note", Static).update("  |  ".join(notes))


class StatusDock(Vertical):
    def __init__(self, *, reduced_motion: bool = False, **kwargs):
        super().__init__(**kwargs)

    def compose(self):
        yield WorkspacePanel(id="dock-workspace")
        with Vertical(id="dock-status-footer"):
            yield Static("IDLE", id="dock-state")
            yield Static("", id="dock-profile")
            yield Static("", id="dock-model")
            yield Static("", id="dock-key")
            yield Static("", id="dock-session")
            yield ProgressBar(total=100, show_eta=False, id="context-bar")
            yield Static("", id="dock-context")
            yield Static("", id="dock-usage")
            yield Static("", id="dock-modes")

    def update_status(self, *, state: str, model: str, profile: str, session: str,
                      context_used: int, context_limit: int, context_trigger: float,
                      input_tokens: int, output_tokens: int, modes: str, task: str,
                      active_file: str = "", active_tool: str = "", key_status: str = ""):
        state_text = state.replace("_", " ").upper()
        active = "  ·  ".join(value for value in (active_tool, active_file) if value)
        status_parts = [state_text]
        if task:
            status_parts.append(task)
        if active:
            status_parts.append(active)
        self.query_one("#dock-state", Static).update("  ·  ".join(status_parts))
        self.query_one("#dock-profile", Static).update(f"Profile  {profile or model}")
        self.query_one("#dock-model", Static).update(f"Model    {model}")
        self.query_one("#dock-key", Static).update(f"Key      {key_status or 'missing'}")
        self.query_one("#dock-session", Static).update(f"Session  {session}")
        percent = 0.0 if not context_limit else min(100.0, context_used / context_limit * 100)
        bar = self.query_one("#context-bar", ProgressBar)
        bar.set_classes("context-danger" if percent >= context_trigger else "context-warning" if percent >= 60 else "context-normal")
        bar.update(progress=percent)
        self.query_one("#dock-context", Static).update(
            f"Context  ≈{context_used:,} / {context_limit:,}  ({percent:.1f}%)"
        )
        self.query_one("#dock-usage", Static).update(f"Tokens   In {input_tokens:,} / Out {output_tokens:,}")
        self.query_one("#dock-modes", Static).update(modes)

    async def update_workspace(self, snapshot: WorkspaceSnapshot):
        await self.query_one(WorkspacePanel).update_snapshot(snapshot)


class WorkspaceModal(ModalScreen[Optional[Dict[str, str]]]):
    ACTIONS = [
        ("Current root", "current"),
        ("Move workspace", "move"),
        ("Save bookmark", "save"),
        ("Remove bookmark", "remove"),
        ("List bookmarks", "list"),
        ("Close", "close"),
    ]

    def __init__(self, snapshot: WorkspaceSnapshot, bookmarks: Optional[List[Dict[str, str]]] = None):
        super().__init__()
        self.snapshot = snapshot
        self.bookmarks = bookmarks or []

    def compose(self):
        with Vertical(id="workspace-modal-shell"):
            yield Static("WORKSPACE - manage and review", id="workspace-modal-title")
            yield Static(self._summary_text(), id="workspace-modal-summary")
            yield ListView(
                *[ListItem(Label(label)) for label, _ in self.ACTIONS],
                initial_index=0,
                id="workspace-actions",
            )
            yield WorkspacePanel(id="modal-workspace")

    async def on_mount(self):
        await self.query_one(WorkspacePanel).update_snapshot(self.snapshot)
        self.query_one("#workspace-actions", ListView).focus()

    def _summary_text(self) -> str:
        lines = [f"Root: {self.snapshot.root}"]
        if self.bookmarks:
            names = ", ".join(str(item.get("name", "")) for item in self.bookmarks[:5])
            suffix = " ..." if len(self.bookmarks) > 5 else ""
            lines.append(f"Bookmarks: {names}{suffix}")
        else:
            lines.append("Bookmarks: none")
        return "\n".join(lines)

    def on_list_view_selected(self, event: ListView.Selected):
        if 0 <= event.index < len(self.ACTIONS):
            label, action = self.ACTIONS[event.index]
            self.dismiss({"action": action, "label": label})

    def on_key(self, event: events.Key):
        if event.key in ("escape", "ctrl+b"):
            event.prevent_default()
            self.dismiss(None)


class ChoiceModal(ModalScreen[int]):
    def __init__(self, title: str, options: List[str], default_index: int = 0):
        super().__init__()
        self.title_text = title
        self.options = options
        self.default_index = default_index

    def compose(self):
        items = [ListItem(Label(option)) for option in self.options]
        yield Static(self.title_text, id="choice-title")
        yield ListView(*items, initial_index=self.default_index, id="choice-list")

    def on_list_view_selected(self, event: ListView.Selected):
        self.dismiss(event.index)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(-1)


class TextPromptModal(ModalScreen[str]):
    def __init__(self, title: str):
        super().__init__()
        self.title_text = title

    def compose(self):
        yield Static(self.title_text, id="prompt-title")
        yield Input(placeholder="Type a response and press Enter", id="prompt-input")

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip())

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss("")


# ---- 0.2.5 Runtime configuration modals --------------------------------------


class ProfileListModal(ModalScreen[Optional[Dict]]):
    """Lists configured profiles; Enter opens edit, 'd' triggers delete, 'c' copies."""

    def __init__(self, profiles: List[Dict[str, Any]], active_id: str = ""):
        super().__init__()
        self.profiles = profiles
        self.active_id = active_id

    def compose(self):
        with Vertical(id="profile-list-shell"):
            yield Static("PROFILES · Enter to edit · 'd' to remove · 'c' to copy · Esc to close", id="profile-list-title")
            items = []
            for profile in self.profiles:
                marker = "* " if profile.get("id") == self.active_id else "  "
                label = f"{marker}{profile.get('id', '')}  -  {profile.get('model', '')}"
                items.append(ListItem(Label(label)))
            yield ListView(*items, initial_index=0, id="profile-list")

    def on_list_view_selected(self, event: ListView.Selected):
        profile = self.profiles[event.index]
        self.dismiss({"action": "edit", "id": profile.get("id", "")})

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "d":
            list_view = self.query_one("#profile-list", ListView)
            if list_view.index is None:
                return
            profile = self.profiles[list_view.index]
            self.dismiss({"action": "delete", "id": profile.get("id", "")})
        elif event.key == "c":
            list_view = self.query_one("#profile-list", ListView)
            if list_view.index is None:
                return
            profile = self.profiles[list_view.index]
            self.dismiss({"action": "copy", "id": profile.get("id", "")})


class ProfileEditorModal(ModalScreen[Optional[Dict]]):
    """Form to add or edit a profile."""

    def __init__(self, *, title: str, defaults: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.title_text = title
        self.defaults = defaults or {}

    def compose(self):
        with Vertical(id="profile-editor-shell"):
            yield Static(self.title_text, id="profile-editor-title")
            yield Label("ID")
            yield Input(value=str(self.defaults.get("id", "")), id="prof-id")
            yield Label("Label (optional)")
            yield Input(value=str(self.defaults.get("label", "")), id="prof-label")
            yield Label("Provider (optional)")
            yield Input(value=str(self.defaults.get("provider", "")), id="prof-provider")
            yield Label("Base URL")
            yield Input(value=str(self.defaults.get("base_url", "")), id="prof-base-url")
            yield Label("API Key env name (optional)")
            yield Input(value=str(self.defaults.get("api_key_env", "")), id="prof-env")
            yield Label("API Key value (optional, will be saved to config.json)")
            yield Input(value=str(self.defaults.get("api_key", "")), password=True, id="prof-key")
            yield Label("Model")
            yield Input(value=str(self.defaults.get("model", "")), id="prof-model")
            yield Label("Context window")
            yield Input(value=str(self.defaults.get("context_window", "")), id="prof-context")
            yield Label("Max tokens")
            yield Input(value=str(self.defaults.get("max_tokens", "")), id="prof-max")
            yield Label("Temperature (0.0 - 2.0)")
            yield Input(value=str(self.defaults.get("temperature", "")), id="prof-temp")
            with Horizontal(id="prof-actions"):
                yield Static("Enter to save · Esc to cancel", id="prof-hint")

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            self._submit()

    def _submit(self):
        values = {
            "id": self.query_one("#prof-id", Input).value.strip(),
            "label": self.query_one("#prof-label", Input).value.strip(),
            "provider": self.query_one("#prof-provider", Input).value.strip(),
            "base_url": self.query_one("#prof-base-url", Input).value.strip(),
            "api_key_env": self.query_one("#prof-env", Input).value.strip(),
            "api_key": self.query_one("#prof-key", Input).value,
            "model": self.query_one("#prof-model", Input).value.strip(),
            "context_window": self.query_one("#prof-context", Input).value.strip(),
            "max_tokens": self.query_one("#prof-max", Input).value.strip(),
            "temperature": self.query_one("#prof-temp", Input).value.strip(),
        }
        self.dismiss(values)


class KeyEditorModal(ModalScreen[Optional[Dict]]):
    """Password input modal for setting a profile API key."""

    def __init__(self, profile_id: str):
        super().__init__()
        self.profile_id = profile_id

    def compose(self):
        with Vertical(id="key-editor-shell"):
            yield Static(f"SET API KEY · {self.profile_id}", id="key-editor-title")
            yield Input(placeholder="Paste API key and press Enter", password=True, id="key-input")
            yield Static("Esc to cancel", id="key-hint")

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss({"profile_id": self.profile_id, "key": event.value.strip()})

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)


class RoleEditorModal(ModalScreen[Optional[Dict]]):
    """Modal to set or clear a model role."""

    def __init__(self, roles: List[str], profiles: List[str], current: Optional[str] = None):
        super().__init__()
        self.roles = roles
        self.profiles = profiles
        self.current = current

    def compose(self):
        with Vertical(id="role-editor-shell"):
            yield Static("SET MODEL ROLE", id="role-editor-title")
            yield Label("Role")
            yield Input(value=self.current or "", id="role-name")
            yield Label("Profile")
            items = [ListItem(Label(p)) for p in self.profiles]
            yield ListView(*items, initial_index=0, id="role-profile-list")
            with Horizontal(id="role-actions"):
                yield Static("Enter to set · 'c' to clear · Esc to cancel", id="role-hint")

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            list_view = self.query_one("#role-profile-list", ListView)
            idx = list_view.index or 0
            profile = self.profiles[idx] if 0 <= idx < len(self.profiles) else ""
            self.dismiss({"action": "set", "role": self.query_one("#role-name", Input).value.strip(), "profile": profile})
        elif event.key == "c":
            self.dismiss({"action": "clear", "role": self.query_one("#role-name", Input).value.strip()})


class ConfirmModal(ModalScreen[bool]):
    """Generic confirmation modal. Default option is Cancel."""

    def __init__(self, message: str, *, default: bool = False):
        super().__init__()
        self.message = message
        self.default = default

    def compose(self):
        with Vertical(id="confirm-shell"):
            yield Static("CONFIRM", id="confirm-title")
            yield Static(self.message, id="confirm-message")
            yield Static("Enter to confirm · Esc to cancel", id="confirm-hint")

    def on_key(self, event: events.Key):
        if event.key == "enter":
            self.dismiss(True)
        elif event.key == "escape":
            self.dismiss(False)


class SearchResultModal(ModalScreen[Optional[int]]):
    """Modal list of search results."""

    def __init__(self, title: str, options: List[str], default_index: int = 0):
        super().__init__()
        self.title_text = title
        self.options = options
        self.default_index = default_index

    def compose(self):
        items = [ListItem(Label(option)) for option in self.options]
        with Vertical(id="search-result-shell"):
            yield Static(self.title_text, id="search-result-title")
            yield ListView(*items, initial_index=self.default_index, id="search-result-list")

    def on_list_view_selected(self, event: ListView.Selected):
        self.dismiss(event.index)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(-1)


class DoctorModal(ModalScreen[None]):
    """Displays the doctor health dashboard."""

    def __init__(self, checks: List[Dict[str, Any]]):
        super().__init__()
        self.checks = checks

    def compose(self):
        lines = [f"{'OK ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}" for c in self.checks]
        ok_count = sum(1 for c in self.checks if c["ok"])
        text = f"Doctor ({ok_count}/{len(self.checks)} checks passed):\n" + "\n".join(lines)
        with Vertical(id="doctor-shell"):
            yield Static("HEALTH DASHBOARD", id="doctor-title")
            yield Static(text, id="doctor-content")
            yield Static("Press Esc or Enter to close", id="doctor-hint")

    def add_checks(self, checks: List[Dict[str, Any]]) -> None:
        """Append additional checks (e.g. async probe results) and refresh text."""
        self.checks.extend(checks)
        lines = [f"{'OK ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}" for c in self.checks]
        ok_count = sum(1 for c in self.checks if c["ok"])
        text = f"Doctor ({ok_count}/{len(self.checks)} checks passed):\n" + "\n".join(lines)
        self.query_one("#doctor-content", Static).update(text)

    def on_key(self, event: events.Key):
        if event.key in ("escape", "enter"):
            self.dismiss(None)


class ProviderListModal(ModalScreen[Optional[Dict]]):
    """Lists configured providers; Enter opens edit, 'd' triggers delete."""

    class Edit(Message):
        def __init__(self, name: str):
            super().__init__()
            self.name = name

    class Delete(Message):
        def __init__(self, name: str):
            super().__init__()
            self.name = name

    def __init__(self, providers: List[Dict[str, Any]], active_name: str = ""):
        super().__init__()
        self.providers = providers
        self.active_name = active_name

    def compose(self):
        with Vertical(id="provider-list-shell"):
            yield Static("PROVIDERS · Enter to edit · 'd' to remove · Esc to close", id="provider-list-title")
            items = []
            for provider in self.providers:
                marker = "* " if provider.get("name") == self.active_name else "  "
                label = f"{marker}{provider.get('name', '')}  -  {provider.get('base_url', '')}"
                items.append(ListItem(Label(label)))
            yield ListView(*items, initial_index=0, id="provider-list")

    def on_list_view_selected(self, event: ListView.Selected):
        provider = self.providers[event.index]
        self.dismiss({"action": "edit", "name": provider.get("name", "")})

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "d":
            list_view = self.query_one("#provider-list", ListView)
            if list_view.index is None:
                return
            provider = self.providers[list_view.index]
            self.dismiss({"action": "delete", "name": provider.get("name", "")})


class ProviderEditorModal(ModalScreen[Optional[Dict]]):
    """Form to add or edit a provider. Dismisses with a values dict or None.

    0.2.6-beta: the API key input is never pre-filled with the existing secret.
    Leaving it blank keeps the existing key; checking "Clear stored key"
    explicitly clears it.
    """

    def __init__(self, *, title: str, defaults: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.title_text = title
        self.defaults = defaults or {}

    def compose(self):
        with Vertical(id="provider-editor-shell"):
            yield Static(self.title_text, id="provider-editor-title")
            yield Label("Name")
            yield Input(value=str(self.defaults.get("name", "")), id="pe-name")
            yield Label("Base URL")
            yield Input(value=str(self.defaults.get("base_url", "")), id="pe-base-url")
            yield Label("API Key env name (blank to skip)")
            yield Input(value=str(self.defaults.get("api_key_env", "")), id="pe-env")
            yield Label("API Key value (leave blank to keep existing key)")
            yield Input(
                value="",
                placeholder="leave blank to keep existing key",
                password=True,
                id="pe-key",
            )
            yield Checkbox("Clear stored API key", id="pe-clear-key")
            with Horizontal(id="pe-actions"):
                yield Static("Enter to save · Esc to cancel", id="pe-hint")

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            self._submit()

    def _submit(self):
        values = {
            "name": self.query_one("#pe-name", Input).value.strip(),
            "base_url": self.query_one("#pe-base-url", Input).value.strip(),
            "api_key_env": self.query_one("#pe-env", Input).value.strip(),
            "api_key": self.query_one("#pe-key", Input).value,
            "clear_key": self.query_one("#pe-clear-key", Checkbox).value,
        }
        self.dismiss(values)


class ModelEditorModal(ModalScreen[Optional[Dict]]):
    """Form to add or edit a model."""

    def __init__(self, *, title: str, provider_name: str, defaults: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.title_text = title
        self.provider_name = provider_name
        self.defaults = defaults or {}

    def compose(self):
        with Vertical(id="model-editor-shell"):
            yield Static(self.title_text, id="model-editor-title")
            yield Label(f"Provider: {self.provider_name}")
            yield Label("Name")
            yield Input(value=str(self.defaults.get("name", "")), id="me-name")
            yield Label("Context window")
            yield Input(value=str(self.defaults.get("context_window", "")), id="me-context")
            yield Label("Max tokens")
            yield Input(value=str(self.defaults.get("max_tokens", "")), id="me-max")
            yield Label("Temperature (0.0 - 2.0)")
            yield Input(value=str(self.defaults.get("temperature", "")), id="me-temp")
            with Horizontal(id="me-actions"):
                yield Static("Enter to save · Esc to cancel", id="me-hint")

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            self._submit()

    def _submit(self):
        defaults = self.defaults or {}
        values = {
            "name": self.query_one("#me-name", Input).value.strip(),
            "context_window": self.query_one("#me-context", Input).value.strip(),
            "max_tokens": self.query_one("#me-max", Input).value.strip(),
            "temperature": self.query_one("#me-temp", Input).value.strip(),
        }
        # Carry forward legacy name if blank (editing case);
        # add-case still needs a name, validated by the caller.
        if not values["name"] and defaults.get("name"):
            values["name"] = defaults["name"]
        self.dismiss(values)


class ConnectionTestModal(ModalScreen[None]):
    """Displays the result of a provider/model health check."""

    def __init__(self, result_text: str, ok: bool):
        super().__init__()
        self.result_text = result_text
        self.ok = ok

    def compose(self):
        with Vertical(id="connection-test-shell"):
            yield Static("CONNECTION TEST" if self.ok else "CONNECTION TEST FAILED", id="ct-title")
            yield Static(self.result_text, id="ct-result")
            yield Static("Press Esc or Enter to close", id="ct-hint")

    def on_key(self, event: events.Key):
        if event.key in ("escape", "enter"):
            self.dismiss(None)


class SecretConfirmModal(ModalScreen[bool]):
    """Inline-API-key safety confirmation. Dismisses True to authorize save."""

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self):
        with Vertical(id="secret-confirm-shell"):
            yield Static("API KEY SECURITY", id="secret-title")
            yield Static(self.message, id="secret-message")
            yield Static("Enter to authorize · Esc to cancel", id="secret-hint")

    def on_key(self, event: events.Key):
        if event.key == "enter":
            self.dismiss(True)
        elif event.key == "escape":
            self.dismiss(False)


class SettingsScreen(ModalScreen[Optional[str]]):
    """Root settings screen. Dismisses with a chosen topic for the app to act on."""

    CHOICES = [
        ("Providers", "providers"),
        ("Models", "models"),
        ("Keys", "keys"),
        ("Roles", "roles"),
        ("Config · validate", "config_validate"),
        ("Config · backup", "config_backup"),
        ("Config · restore", "config_restore"),
        ("Config · import", "config_import"),
        ("Config · export", "config_export"),
        ("Doctor", "doctor"),
        ("Close", "close"),
    ]

    def __init__(self):
        super().__init__()

    def compose(self):
        with Vertical(id="settings-shell"):
            yield Static("SETTINGS · choose an area", id="settings-title")
            items = [ListItem(Label(label)) for label, _ in self.CHOICES]
            yield ListView(*items, initial_index=0, id="settings-list")

    def on_list_view_selected(self, event: ListView.Selected):
        if 0 <= event.index < len(self.CHOICES):
            self.dismiss(self.CHOICES[event.index][1])

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss("close")


class ModeModal(ModalScreen[Optional[Dict]]):
    """Compact modal for switching authorization, plan, and thinking modes."""

    AUTH_OPTIONS = ["Manual", "Auto", "YOLO"]

    def __init__(self, current: Optional[Dict[str, Any]] = None):
        super().__init__()
        self.current = current or {}

    def compose(self):
        with Vertical(id="mode-shell"):
            yield Static("MODE", id="mode-title")
            yield Static("Authorization", classes="mode-section-title")
            items = [ListItem(Label(option)) for option in self.AUTH_OPTIONS]
            auth_index = self._auth_index()
            yield ListView(*items, initial_index=auth_index, id="mode-auth-list")
            yield Static("Plan Mode", classes="mode-section-title")
            yield Checkbox("ON", value=bool(self.current.get("plan", False)), id="mode-plan")
            yield Static("Thinking Mode", classes="mode-section-title")
            yield Checkbox("ON", value=bool(self.current.get("thinking", False)), id="mode-thinking")
            yield Static("Enter to save · Esc to cancel", id="mode-hint")

    def _auth_index(self) -> int:
        level = str(self.current.get("authorization", "manual")).lower()
        try:
            return self.AUTH_OPTIONS.index(level.capitalize())
        except ValueError:
            return 0

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            self._submit()

    def _submit(self):
        auth_list = self.query_one("#mode-auth-list", ListView)
        idx = auth_list.index or 0
        authorization = self.AUTH_OPTIONS[idx].lower()
        plan = self.query_one("#mode-plan", Checkbox).value
        thinking = self.query_one("#mode-thinking", Checkbox).value
        self.dismiss({"authorization": authorization, "plan": plan, "thinking": thinking})


class SetupWizardModal(ModalScreen[Optional[Dict]]):
    """Multi-step first-time setup wizard for a provider/profile.

    Dismisses with a values dict or None if cancelled.
    """

    STEPS = [
        ("name", "Profile / Provider name", "e.g. openai"),
        ("base_url", "Base URL", "https://api.openai.com/v1"),
        ("model", "Model name", "gpt-4o"),
        ("key_mode", "API key mode", None),
        ("key_value", "API key value or env name", "paste key or env var name"),
        ("context_window", "Context window", "128000"),
        ("max_tokens", "Max tokens", "4000"),
        ("temperature", "Temperature", "0.7"),
        ("summary", "Review and save", None),
    ]

    def __init__(self):
        super().__init__()
        self.step = 0
        self.values: Dict[str, Any] = {}

    def compose(self):
        with Vertical(id="setup-wizard-shell"):
            yield Static("SETUP WIZARD", id="setup-wizard-title")
            yield Static("", id="setup-wizard-step-title")
            yield Input(id="setup-wizard-input")
            yield ListView(id="setup-wizard-choices")
            yield Static("", id="setup-wizard-summary")
            yield Static("", id="setup-wizard-hint")
            yield Static("", id="setup-wizard-progress")

    def on_mount(self):
        self._render_step()

    def _render_step(self):
        key, title, placeholder = self.STEPS[self.step]
        self.query_one("#setup-wizard-step-title", Static).update(f"{self.step + 1}/{len(self.STEPS)} · {title}")
        input_widget = self.query_one("#setup-wizard-input", Input)
        choices = self.query_one("#setup-wizard-choices", ListView)
        summary = self.query_one("#setup-wizard-summary", Static)
        hint = self.query_one("#setup-wizard-hint", Static)
        progress = self.query_one("#setup-wizard-progress", Static)
        summary.display = False
        if key == "key_mode":
            input_widget.display = False
            choices.display = True
            choices.clear()
            choices.extend([ListItem(Label("Inline API key (saved to config.json)")), ListItem(Label("Env var API key"))])
            choices.index = 0
            hint.update("Choose how Kairo reads the API key")
        elif key == "summary":
            input_widget.display = False
            choices.display = False
            lines = [
                f"Profile: {self.values.get('name', '')}",
                f"Base URL: {self.values.get('base_url', '')}",
                f"Model: {self.values.get('model', '')}",
                f"Key mode: {self.values.get('key_mode', '')}",
                f"Key value: {self._masked_key_value()}",
                f"Context window: {self.values.get('context_window', '')}",
                f"Max tokens: {self.values.get('max_tokens', '')}",
                f"Temperature: {self.values.get('temperature', '')}",
            ]
            summary.update("\n".join(lines))
            summary.display = True
            hint.update("Enter to save · Shift+Enter to go back · Esc to cancel")
        else:
            input_widget.display = True
            input_widget.disabled = False
            input_widget.placeholder = placeholder or ""
            input_widget.value = str(self.values.get(key, ""))
            input_widget.password = key == "key_value" and self.values.get("key_mode") == "inline"
            choices.display = False
            choices.clear()
            hint.update("Enter to continue · Shift+Enter to go back · Esc to cancel")
        progress.update(f"Step {self.step + 1} of {len(self.STEPS)}")
        input_widget.focus()

    def _masked_key_value(self) -> str:
        value = str(self.values.get("key_value", ""))
        if not value:
            return "(none)"
        if len(value) <= 8:
            return "*" * len(value)
        return value[:4] + "..." + value[-4:]

    def on_input_submitted(self, event: Input.Submitted):
        if self.query_one("#setup-wizard-input", Input).disabled:
            return
        self._advance(event.value.strip())

    def on_list_view_selected(self, event: ListView.Selected):
        key = self.STEPS[self.step][0]
        if key != "key_mode":
            return
        mode = "inline" if event.index == 0 else "env"
        self._advance(mode)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            key = self.STEPS[self.step][0]
            if key == "summary":
                self._finish()
            elif key == "key_mode":
                choices = self.query_one("#setup-wizard-choices", ListView)
                if choices.index is not None:
                    mode = "inline" if choices.index == 0 else "env"
                    self._advance(mode)
            else:
                value = self.query_one("#setup-wizard-input", Input).value.strip()
                self._advance(value)
        elif event.key == "shift+enter":
            if self.step > 0:
                self.step -= 1
                self._render_step()

    def _advance(self, value: str):
        key = self.STEPS[self.step][0]
        if key in ("context_window", "max_tokens"):
            try:
                int(value)
            except ValueError:
                value = self._default_for(key)
        elif key == "temperature":
            try:
                float(value)
            except ValueError:
                value = self._default_for(key)
        self.values[key] = value
        if self.step + 1 < len(self.STEPS):
            self.step += 1
            self._render_step()
        else:
            self._finish()

    def _default_for(self, key: str) -> str:
        for step_key, _, placeholder in self.STEPS:
            if step_key == key:
                return placeholder or "0"
        return "0"

    def _finish(self):
        self.values["api_key_mode"] = self.values.get("key_mode", "inline")
        self.values["api_key_value"] = self.values.get("key_value", "")
        self.dismiss(dict(self.values))


class ExportModal(ModalScreen[Optional[Dict]]):
    """Export options for sessions and config."""

    OPTIONS = [
        ("Session as Markdown", "session_markdown"),
        ("Session as JSON", "session_json"),
        ("Config (redacted)", "config_redacted"),
        ("Config with keys", "config_with_keys"),
        ("Cancel", "cancel"),
    ]

    def compose(self):
        with Vertical(id="export-shell"):
            yield Static("EXPORT", id="export-title")
            items = [ListItem(Label(label)) for label, _ in self.OPTIONS]
            yield ListView(*items, initial_index=0, id="export-list")
            yield Static("Enter to choose · Esc to cancel", id="export-hint")

    def on_list_view_selected(self, event: ListView.Selected):
        self._choose(event.index)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            list_view = self.query_one("#export-list", ListView)
            if list_view.index is not None:
                self._choose(list_view.index)

    def _choose(self, index: int):
        if index is None or index < 0 or index >= len(self.OPTIONS):
            self.dismiss(None)
            return
        action = self.OPTIONS[index][1]
        if action == "cancel":
            self.dismiss(None)
            return
        self.dismiss({"action": action})
