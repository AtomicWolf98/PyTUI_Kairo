"""CLI argument parsing (pure logic; no app boot)."""

from __future__ import annotations

import pytest

from kairo_tui.cli import CliOptions, parse_args


def test_parse_args_defaults() -> None:
    assert parse_args([]) == CliOptions()


def test_parse_args_workspace_positional() -> None:
    assert parse_args([r"C:\work\demo"]).workspace == r"C:\work\demo"


def test_parse_args_flags() -> None:
    options = parse_args(
        ["--config", "cfg.json", "--theme", "dark", "--reduced-motion", "--safe-mode", "--headless-smoke"]
    )
    assert options.config_path == "cfg.json"
    assert options.theme == "dark"
    assert options.reduced_motion is True
    assert options.safe_mode is True
    assert options.headless_smoke is True


def test_parse_args_unknown_flag_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--bogus"])
    assert exc.value.code == 2


def test_main_help_exits_0() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])
    assert exc.value.code == 0
