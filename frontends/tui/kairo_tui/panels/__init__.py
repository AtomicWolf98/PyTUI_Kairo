"""V2 panels: opt-in sidebars on the chat-first shell."""

from kairo_tui.panels.context import ContextPanel
from kairo_tui.panels.diagnostics import DiagnosticsPanel
from kairo_tui.panels.extensions import ExtensionsPanel
from kairo_tui.panels.memory import MemoryPanel
from kairo_tui.panels.settings import SettingsPanel
from kairo_tui.panels.workspace import WorkspacePanel

__all__ = [
    "ContextPanel",
    "DiagnosticsPanel",
    "ExtensionsPanel",
    "MemoryPanel",
    "SettingsPanel",
    "WorkspacePanel",
]
