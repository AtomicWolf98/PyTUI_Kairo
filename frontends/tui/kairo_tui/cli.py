"""Command-line entry point for kairo-tui (V2 development shell)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from kairo_kernel import KairoKernel


@dataclass(frozen=True)
class CliOptions:
    """Parsed invocation; no secrets, no resolved paths."""

    workspace: str | None = None
    config_path: str | None = None
    safe_mode: bool = False
    headless_smoke: bool = False


def parse_args(argv: Sequence[str] | None = None) -> CliOptions:
    parser = argparse.ArgumentParser(
        prog="kairo-tui",
        description="Kairo chat-first TUI (v2).",
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
        help="Path to the global config-v1.json document.",
    )
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        help="Open in-memory: no persistence, no automatic MCP connection.",
    )
    parser.add_argument("--headless-smoke", action="store_true", help="Run the headless smoke gate and exit.")
    parser.add_argument("--version", action="version", version="kairo-tui 0.4.0a2")
    parsed = parser.parse_args(argv)
    return CliOptions(parsed.workspace, parsed.config_path, parsed.safe_mode, parsed.headless_smoke)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the V2 app. A failed kernel open never blocks input."""
    from kairo_tui.app import KairoTuiApp
    from kairo_tui.bootstrap import open_tui_kernel
    from kairo_tui.smoke import run_smoke

    options = parse_args(argv)
    if options.headless_smoke:
        return asyncio.run(run_smoke())
    kernel: KairoKernel | None = None
    opened = asyncio.run(open_tui_kernel(options))
    if opened.ok and opened.value is not None:
        kernel = opened.value.kernel
    elif opened.error is not None:
        print(f"Warning: {opened.error.message}", file=sys.stderr)
    KairoTuiApp(kernel=kernel).run()
    return 0
