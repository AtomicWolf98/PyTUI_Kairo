"""Workspace sidebar: changed files and tree summary via the kernel."""

from __future__ import annotations

from textual.widgets import Static

from kairo_tui.state import AppState, WorkspaceView


class WorkspacePanel(Static):
    """Shows kernel workspace facts; never runs git itself."""

    def render_state(self, state: AppState) -> None:
        lines = ["[b]Workspace[/b]"]
        workspace = state.workspace
        if workspace is None:
            lines.append("Not connected.")
            self.update("\n".join(lines))
            return
        lines.append(f"Root: {workspace.root}")
        lines.append(f"Revision: {workspace.revision}")
        for path in self._changed_files:
            lines.append(f"• {path}")
        self.update("\n".join(lines))

    def set_changed_files(self, paths: tuple[str, ...]) -> None:
        self._changed_files = paths

    def render_workspace(self, workspace: WorkspaceView, changed: tuple[str, ...]) -> None:
        self._changed_files = changed
        self.render_state(AppState(workspace=workspace))
