"""Workspace page model: is_stale matrix and changed-row labels.

Pure unit tests (no Textual); the screen tests cover the widget wiring.
``change_button_id`` (id sanitization) is exercised end-to-end by the
changed-files screen tests, which build their query ids with it.
"""

from __future__ import annotations

from dataclasses import dataclass

from kairo_tui.store import AppState
from kairo_tui.structs import ChangedFileLike
from kairo_tui.workspace_model import ChangedRow, changed_rows, is_stale


@dataclass
class _FakeChangedFile:
    relative_path: str
    status: str


@dataclass
class _FakeChangedFiles:
    root: str = ""
    revision: int = 0
    is_git_repository: bool = True
    # Annotated with the protocol types so the structural match is exact
    # (mypy requires writable protocol members to match, not just be subtypes).
    files: tuple[ChangedFileLike, ...] = ()


def test_is_stale_matrix() -> None:
    """Same root + revision is fresh; either changing makes the response stale."""
    state = AppState(workspace_root="/root", workspace_revision=3)
    assert is_stale("/root", 3, state) is False
    assert is_stale("/other", 3, state) is True  # workspace moved
    assert is_stale("/root", 4, state) is True   # workspace mutated


def test_changed_rows_labels() -> None:
    result = _FakeChangedFiles(
        files=(
            _FakeChangedFile("a.py", "modified"),
            _FakeChangedFile("b.txt", "untracked"),
            _FakeChangedFile("c", "renamed"),
            _FakeChangedFile("d", "unknown-status"),
        )
    )
    assert changed_rows(result) == (
        ChangedRow("a.py", "M a.py"),
        ChangedRow("b.txt", "U b.txt"),
        ChangedRow("c", "R c"),
        ChangedRow("d", "? d"),
    )
