"""V2 bootstrap: open the kernel exactly once through the public API."""

from __future__ import annotations

from pathlib import Path

from kairo_kernel import KernelOpenOptions, OpenedKernel, open_kernel
from kairo_kernel.errors import KernelResult
from platformdirs import user_config_dir

from kairo_tui_v2.cli import CliOptions


def default_config_path() -> str:
    """Per-user config-v1.json; the kernel owns parsing, never the frontend."""
    return str(Path(user_config_dir("kairo")) / "config-v1.json")


async def open_tui_kernel(options: CliOptions) -> KernelResult[OpenedKernel]:
    """One public call; no service imports, no seeded roles, no setup flags."""
    return await open_kernel(
        KernelOpenOptions(
            workspace_root=options.workspace or ".",
            config_path=options.config_path or default_config_path(),
            safe_mode=options.safe_mode,
        )
    )
