"""Official size matrix (tui_plan.md): 80x24, 100x30, 140x40, 200x50."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import DraftAction, PageAction, PageId

SIZES = [(80, 24, "overlay"), (100, 30, "narrow"), (140, 40, "full"), (200, 50, "full")]


@pytest.fixture
def matrix_app_factory(workspace: Path):
    def make(*, size: tuple[int, int]) -> KairoTuiApp:
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace),
                             config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
        )
        return KairoTuiApp(bootstrap)
    return make


@pytest.mark.parametrize("width,height,breakpoint", SIZES)
def test_layout_class_per_size(matrix_app_factory, width, height, breakpoint) -> None:
    app = matrix_app_factory(size=(width, height))

    async def drive() -> None:
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == breakpoint
            # Every page mounts without crashing at this size.
            for page in (PageId.CHAT, PageId.SESSIONS, PageId.WORKSPACE, PageId.MEMORY,
                         PageId.EXTENSIONS, PageId.SETTINGS, PageId.DOCTOR):
                app.store.dispatch(PageAction(page))
                await pilot.pause()
            assert app.query_one("#topbar") is not None
            assert app.query_one("#composer") is not None

    asyncio.run(drive())


def test_composer_draft_preserved_across_matrix(matrix_app_factory) -> None:
    app = matrix_app_factory(size=(200, 50))

    async def drive() -> None:
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            app.store.dispatch(DraftAction("matrix draft"))
            for width, height, _ in SIZES:
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                assert app.store.state.draft == "matrix draft"

    asyncio.run(drive())
