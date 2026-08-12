"""V2 bootstrap: open the kernel exactly once through the public API."""

from __future__ import annotations

from kairo_kernel import KernelOpenOptions, OpenedKernel, open_kernel
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports import SecretPort

from kairo_tui.cli import CliOptions
from kairo_tui.keyring_store import KeyringSecretPort, make_secret_store
from kairo_tui.paths import default_trust_dir, resolve_config_path


def make_secret_port() -> SecretPort:
    """Persistent keyring-backed secrets; env-reference fallback, never plaintext."""
    return KeyringSecretPort(make_secret_store(safe_mode=False))


async def open_tui_kernel(options: CliOptions) -> KernelResult[OpenedKernel]:
    """One public call; no service imports, no seeded roles, no setup flags."""
    trust_dir = default_trust_dir()
    trust_dir.mkdir(parents=True, exist_ok=True)
    return await open_kernel(
        KernelOpenOptions(
            workspace_root=options.workspace or ".",
            config_path=str(resolve_config_path(options.config_path)),
            safe_mode=options.safe_mode,
        ),
        secrets=make_secret_port(),
    )
