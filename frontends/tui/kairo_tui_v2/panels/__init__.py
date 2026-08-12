"""V2 panels: opt-in sidebars on the chat-first shell."""

from kairo_tui_v2.panels.context import ContextPanel
from kairo_tui_v2.panels.diagnostics import DiagnosticsPanel
from kairo_tui_v2.panels.extensions import ExtensionsPanel
from kairo_tui_v2.panels.memory import MemoryPanel
from kairo_tui_v2.panels.settings import SettingsPanel
from kairo_tui_v2.panels.workspace import WorkspacePanel

__all__ = [
    "ContextPanel",
    "DiagnosticsPanel",
    "ExtensionsPanel",
    "MemoryPanel",
    "SettingsPanel",
    "WorkspacePanel",
]
