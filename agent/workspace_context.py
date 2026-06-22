"""Single source of truth for the active workspace root.

All filesystem-aware components (file tools, patch tools, shell session,
Python REPL, skills loader, workspace monitor, UI header) receive a
``WorkspaceContext`` instance and register listeners so that ``/workspace move``
can update every consumer atomically.
"""
import tempfile
from pathlib import Path
from typing import Callable, List


class WorkspaceMoveError(Exception):
    """Raised when a workspace move cannot be completed."""


class WorkspaceContext:
    """Holds the canonical workspace root and notifies listeners of changes."""

    def __init__(self, root: str | Path, allow_absolute_outside: bool = False):
        self._root = Path(root).expanduser().resolve()
        self.allow_absolute_outside = allow_absolute_outside
        self._listeners: List[Callable[[Path], None]] = []

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str | Path) -> Path:
        """Return an absolute path relative to the workspace root."""
        return self._root / Path(path)

    def add_listener(self, callback: Callable[[Path], None]) -> None:
        """Register a callback invoked with the new root after a successful move."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Path], None]) -> None:
        """Unregister a previously added move listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def move(self, target: str | Path) -> None:
        """Atomically move the workspace root to *target* and notify listeners.

        Raises:
            WorkspaceMoveError: If the target does not exist, is not a directory,
                or is not writable.
        """
        target_path = Path(target).expanduser().resolve()
        if not target_path.exists():
            raise WorkspaceMoveError(f"path does not exist: {target_path}")
        if not target_path.is_dir():
            raise WorkspaceMoveError(f"path is not a directory: {target_path}")

        try:
            with tempfile.NamedTemporaryFile(dir=target_path, delete=True):
                pass
        except Exception as exc:
            raise WorkspaceMoveError(f"directory is not writable: {target_path} ({exc})") from exc

        self._root = target_path
        for listener in list(self._listeners):
            try:
                listener(target_path)
            except Exception:
                # Listener errors must not break the move transaction.
                pass
