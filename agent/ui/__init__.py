"""Kairo Textual user interface."""

from agent.ui.windows_keys import install_windows_modified_enter_support

install_windows_modified_enter_support()

from agent.ui.app import KairoApp

__all__ = ["KairoApp"]
