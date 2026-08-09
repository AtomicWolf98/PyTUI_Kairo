"""Pure view-model for the Workspace page.

Textual-free: the Workspace screen maps each ``ChangedRow`` to a row Button 1:1
and drops every read whose ``root``/``revision`` no longer matches the store
(``is_stale`` — tui_plan.md revision contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from kairo_tui.store import AppState
from kairo_tui.structs import ChangedFilesLike


def is_stale(root: str, revision: int, state: AppState) -> bool:
    """Drop the response when the workspace moved or was mutated meanwhile."""
    return root != state.workspace_root or revision != state.workspace_revision


STATUS_LABEL = {"modified": "M", "added": "A", "deleted": "D", "renamed": "R", "untracked": "U"}


@dataclass(frozen=True)
class ChangedRow:
    relative_path: str
    label: str  # "M path" etc.


def changed_rows(result: ChangedFilesLike) -> tuple[ChangedRow, ...]:
    return tuple(ChangedRow(f.relative_path, f"{STATUS_LABEL.get(f.status, '?')} {f.relative_path}")
                 for f in result.files)


def change_button_id(relative_path: str) -> str:
    """Sanitize a path into a Textual widget id (letters/digits/_/- only).

    Textual rejects ids containing dots or slashes (``BadIdentifier``), so the
    changed-file row buttons use this id; the screen keeps a sanitized→path map
    to resolve the real relative path on press.
    """
    return "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in relative_path)
