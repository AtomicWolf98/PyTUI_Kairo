"""--headless-smoke end-to-end."""

from __future__ import annotations

from pathlib import Path

from kairo_tui.cli import CliOptions
from kairo_tui.smoke import run_headless_smoke


def test_headless_smoke_ok(workspace: Path) -> None:
    code = run_headless_smoke(
        CliOptions(workspace=str(workspace), config_path=str(workspace.parent / "config-v1.json"))
    )
    assert code == 0
