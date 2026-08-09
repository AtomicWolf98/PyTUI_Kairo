"""Textual TUI frontend for Kairo (kairo-tui).

This module must stay importable without ``kairo_kernel`` or Textual so that
setuptools can read the version attribute in an isolated build environment.
"""

from kairo_tui._version import __version__

__all__ = ["__version__"]
