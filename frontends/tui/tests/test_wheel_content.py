"""Wheel content gates for the V2 TUI package (R0)."""

from __future__ import annotations

import os
import zipfile


def _wheel_path() -> str:
    path = os.environ.get("KAIRO_TUI_WHEEL", "dist/kairo_tui-0.4.0a2-py3-none-any.whl")
    assert os.path.exists(path), f"wheel not found: {path}"
    return path


def test_wheel_contains_v2_package_layout() -> None:
    with zipfile.ZipFile(_wheel_path()) as wheel:
        names = set(wheel.namelist())
        for required in (
            "kairo_tui/app.py",
            "kairo_tui/cli.py",
            "kairo_tui/bootstrap.py",
            "kairo_tui/controller.py",
            "kairo_tui/state.py",
            "kairo_tui/reducer.py",
            "kairo_tui/theme.tcss",
            "kairo_tui/dialogs/connect.py",
            "kairo_tui/widgets/composer.py",
            "kairo_tui/panels/context.py",
        ):
            assert required in names, f"missing {required}"
        assert "kairo_tui/screens/" not in names
        assert not any(name.startswith("kairo_tui/screens") for name in names)
        assert not any("kairo_tui_v2" in name for name in names)


def test_wheel_entrypoints_point_at_v2_cli() -> None:
    with zipfile.ZipFile(_wheel_path()) as wheel:
        metadata = wheel.read("kairo_tui-0.4.0a2.dist-info/entry_points.txt").decode("utf-8")
        assert "kairo-tui = kairo_tui.cli:main" in metadata
        assert "kairo = kairo_tui.cli:main" in metadata


def test_wheel_has_no_legacy_gates() -> None:
    with zipfile.ZipFile(_wheel_path()) as wheel:
        for name in wheel.namelist():
            if name.endswith(".py"):
                source = wheel.read(name).decode("utf-8")
                assert "SetupScreen" not in source, name
                assert "setup_complete" not in source, name
                assert "composer.disabled = " not in source, name  # assignment gate, not an assertion
