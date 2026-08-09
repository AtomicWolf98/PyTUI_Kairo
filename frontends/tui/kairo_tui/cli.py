"""Command-line entry point for kairo-tui."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CliOptions:
    """Parsed ``kairo-tui`` invocation; no secrets, no paths resolved yet."""

    workspace: str | None = None
    config_path: str | None = None
    theme: str | None = None
    reduced_motion: bool = False
    safe_mode: bool = False
    headless_smoke: bool = False


def parse_args(argv: Sequence[str] | None = None) -> CliOptions:
    parser = argparse.ArgumentParser(
        prog="kairo-tui",
        description="Kairo Textual TUI (kairo-tui 0.4.0a2).",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        default=None,
        help="Workspace root (default: current directory).",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        metavar="PATH",
        help="Path to the global config-v1.json document (default: platformdirs user config dir).",
    )
    parser.add_argument("--theme", default=None, metavar="NAME", help="Theme name.")
    parser.add_argument("--reduced-motion", action="store_true", help="Disable animations.")
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        help="Force Manual authorization, disable MCP auto-connect and persisted settings writes.",
    )
    parser.add_argument(
        "--headless-smoke",
        action="store_true",
        help="Run the deterministic headless smoke check and exit.",
    )
    parsed = parser.parse_args(argv)
    return CliOptions(
        workspace=parsed.workspace,
        config_path=parsed.config_path,
        theme=parsed.theme,
        reduced_motion=parsed.reduced_motion,
        safe_mode=parsed.safe_mode,
        headless_smoke=parsed.headless_smoke,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point; returns the process exit code."""
    options = parse_args(argv)
    if options.headless_smoke:
        from kairo_tui.smoke import run_headless_smoke

        return run_headless_smoke(options)
    from kairo_tui.app import KairoTuiApp

    KairoTuiApp.from_options(options).run()
    return 0
