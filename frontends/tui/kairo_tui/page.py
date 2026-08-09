"""Shared page utilities: keep ``state.sessions`` fresh across pages.

The store learns sessions only from ``RecoveryAction`` and explicit
``SessionsAction`` dispatches (documented store gap: ``fold_event`` handles
Turn/Message/Tool/Interaction/ChangeEvent(WORKSPACE) only), so every page
that shows sessions must re-list via ``kernel.sessions.list()`` after
bootstrap and after any session mutation.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace

from kairo_tui.config_document import ConfigDocumentAdapter
from kairo_tui.store import ConfigAction, SessionsAction


async def refresh_sessions(app) -> None:
    """Re-list sessions into the store after any session mutation.

    Best-effort dispatch: the reducer commits ``state.sessions`` before any
    listener runs, so a raising listener (e.g. a screen whose unmount is still
    settling after a page switch) never loses the update — pages re-render
    from live store state on their next flush.
    """
    result = await app.kernel.sessions.list()
    if result.ok and result.value is not None:
        with suppress(Exception):
            app.store.dispatch(SessionsAction(result.value))


def record_recent_workspace(app, root: str) -> None:
    """Prepend ``root`` (deduped, capped at 5) to the document's recent workspaces.

    Workbench plan Task 6 helper (``page.py`` extension); the Workspace page
    move path already calls it so a successful switch is recorded immediately.
    Persists via the config adapter and reflects the change in the store.
    """
    document = app.store.state.document
    recent = tuple(dict.fromkeys([root, *document.recent_workspaces]))[:5]
    updated = replace(document, recent_workspaces=recent)
    ConfigDocumentAdapter(app._bootstrap.config_path, safe_mode=app.store.state.safe_mode).save(updated)
    with suppress(Exception):
        app.store.dispatch(ConfigAction(updated, app.store.state.setup_complete))
