"""Deterministic headless smoke check for ``kairo-tui --headless-smoke``."""

from __future__ import annotations

import asyncio

from kairo_tui.cli import CliOptions


def run_headless_smoke(options: CliOptions) -> int:
    """Boot the real kernel + app headless, drive a scripted Pilot, exit 0/1.

    The app is bootstrapped synchronously (before any loop) and only the Pilot
    drive runs inside ``asyncio.run`` — never nest ``asyncio.run`` calls.
    """
    from kairo_tui.app import KairoTuiApp
    from kairo_tui.store import DraftAction

    app = KairoTuiApp.from_options(options)

    async def drive() -> int:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # Empty document ⇒ Setup page is the default and send is disabled.
            if not app.store.state.setup_complete:
                from kairo_tui.screens.setup import SetupScreen
                if app.query_one("#page").query(SetupScreen) is None:
                    raise AssertionError("Setup page not shown for empty configuration.")
            composer = app.query_one("#composer")
            if composer.disabled is not (not app.store.state.setup_complete):
                raise AssertionError("Composer send gating is inconsistent.")
            # Resize matrix sanity: compat layout never crashes and keeps the draft.
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            draft = "smoke draft"
            app.store.dispatch(DraftAction(draft))
            await pilot.resize_terminal(200, 50)
            await pilot.pause()
            if app.store.state.draft != draft:
                raise AssertionError("Draft was lost across resizes.")
        print("KAIRO_TUI_SMOKE_OK")
        return 0

    return asyncio.run(drive())
