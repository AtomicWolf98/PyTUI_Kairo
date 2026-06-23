from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rich.syntax import Syntax
from rich.text import Text
from textual import events
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Markdown, ProgressBar, Static, TextArea, Tree

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
                f"[bold #f5f7fa]KAIRO[/bold #f5f7fa] [#7f849c]v0.2.3[/#7f849c]\n"
                f"[#a5adcb]{self.profile or self.model}[/#a5adcb]  [#6e738d]({self.model})[/#6e738d]\n"
                f"[#7f849c]{self.cwd}[/#7f849c]"
            ),
            id="brand-meta",
        )

    def update_meta(self, model: str, profile: str, cwd: str):
        self.model, self.profile, self.cwd = model, profile, cwd
        self.query_one("#brand-meta", Static).update(
            Text.from_markup(
                f"[bold #f5f7fa]KAIRO[/bold #f5f7fa] [#7f849c]v0.2.3[/#7f849c]\n"
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
            yield Static("", id="dock-model")
            yield Static("", id="dock-session")
            yield ProgressBar(total=100, show_eta=False, id="context-bar")
            yield Static("", id="dock-context")
            yield Static("", id="dock-usage")
            yield Static("", id="dock-modes")

    def update_status(self, *, state: str, model: str, profile: str, session: str,
                      context_used: int, context_limit: int, context_trigger: float,
                      input_tokens: int, output_tokens: int, modes: str, task: str,
                      active_file: str = "", active_tool: str = ""):
        state_text = state.replace("_", " ").upper()
        active = "  ·  ".join(value for value in (active_tool, active_file) if value)
        status_parts = [state_text]
        if task:
            status_parts.append(task)
        if active:
            status_parts.append(active)
        self.query_one("#dock-state", Static).update("  ·  ".join(status_parts))
        self.query_one("#dock-model", Static).update(f"Model  {profile or model}")
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


class WorkspaceModal(ModalScreen[None]):
    def __init__(self, snapshot: WorkspaceSnapshot):
        super().__init__()
        self.snapshot = snapshot

    def compose(self):
        with Vertical(id="workspace-modal-shell"):
            yield Static("WORKSPACE  ·  read-only review", id="workspace-modal-title")
            yield WorkspacePanel(id="modal-workspace")

    async def on_mount(self):
        await self.query_one(WorkspacePanel).update_snapshot(self.snapshot)
        self.query_one(WorkspaceTree).focus()

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


# ---- 0.2.3 Runtime configuration modals --------------------------------------


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
    """Form to add or edit a provider. Dismisses with a values dict or None."""

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
            yield Label("API Key value (blank to keep env only)")
            yield Input(value=str(self.defaults.get("api_key", "")), password=True, id="pe-key")
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

    def __init__(self):
        super().__init__()

    def compose(self):
        with Vertical(id="settings-shell"):
            yield Static("SETTINGS · choose an area", id="settings-title")
            yield ListView(
                ListItem(Label("Manage providers")),
                ListItem(Label("Add model")),
                ListItem(Label("Edit model")),
                ListItem(Label("Remove model")),
                ListItem(Label("Test model")),
                ListItem(Label("Validate config")),
                ListItem(Label("Create config backup")),
                ListItem(Label("Restore config backup")),
                ListItem(Label("Close")),
                id="settings-list",
            )

    def on_list_view_selected(self, event: ListView.Selected):
        mapping = [
            "providers",
            "model_add",
            "model_edit",
            "model_remove",
            "model_test",
            "config_validate",
            "config_backup",
            "config_restore",
            "close",
        ]
        if 0 <= event.index < len(mapping):
            self.dismiss(mapping[event.index])

    def on_key(self, event: events.Key):
        if event.key == "escape":
            self.dismiss("close")
