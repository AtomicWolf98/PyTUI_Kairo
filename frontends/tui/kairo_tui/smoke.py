"""Headless smoke gate for installed wheels (R0).

Verifies: kernel open/start/status/shutdown, app compose, composer enabled
and focusable without any provider, and no leftover workers/subscriptions.
Prints the unique marker ``KAIRO_TUI_SMOKE_OK`` on success.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

SMOKE_MARKER = "KAIRO_TUI_SMOKE_OK"


async def run_smoke() -> int:
    from kairo_kernel import KernelOpenOptions, open_kernel

    from kairo_tui.app import KairoTuiApp
    from kairo_tui.widgets.composer import Composer

    config_path = os.path.join(tempfile.gettempdir(), f"kairo-tui-smoke-{os.getpid()}.json")
    opened = await open_kernel(
        KernelOpenOptions(workspace_root=os.getcwd(), config_path=config_path)
    )
    if not opened.ok or opened.value is None:
        print("SMOKE_FAIL kernel open")
        return 1
    kernel = opened.value.kernel
    status = await kernel.status()
    assert status.state.value == "running", "kernel not running"
    try:
        app = KairoTuiApp(kernel=kernel)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", Composer)
            assert composer is not None
            assert not composer.disabled, "composer must be enabled"
            assert composer.can_focus, "composer must be focusable"
            await pilot.pause()
            assert app.focused is composer, "composer must own focus"
        if app._event_loop is not None:
            await app._event_loop.close()
        # No leftover workers or subscriptions after close.
        for worker in app.workers:
            assert worker.is_finished or worker.is_cancelled, f"leftover worker {worker.name}"
        if app._event_loop is not None:
            assert app._event_loop._task is None or app._event_loop._task.done(), "leftover event loop task"
    finally:
        await kernel.shutdown()
    if os.path.exists(config_path):
        os.unlink(config_path)
    print(SMOKE_MARKER)
    return 0


def main() -> int:
    return asyncio.run(run_smoke())


if __name__ == "__main__":
    raise SystemExit(main())
