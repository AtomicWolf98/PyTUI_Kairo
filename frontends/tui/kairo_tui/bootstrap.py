"""Compose and start the kernel from CLI options + config document + secrets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from kairo_kernel import KairoKernel, KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.lifecycle import LifecycleState  # noqa: F401
from kairo_kernel.errors import KernelResult
from kairo_kernel.ports.providers import ProviderPort
from kairo_kernel.ports.tools import ToolRegistryPort

from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter
from kairo_tui.keyring_store import KeyringSecretPort, SecretStore, make_secret_store
from kairo_tui.paths import default_trust_dir, resolve_config_path
from kairo_tui.store import AppState, AppStore, PageId


@dataclass(frozen=True)
class BootstrapOptions:
    workspace_root: str
    config_path: Path | None = None
    theme: str | None = None
    reduced_motion: bool = False
    safe_mode: bool = False


@dataclass(frozen=True)
class BootstrapResult:
    kernel: KairoKernel
    document: ConfigDocument
    store: AppStore
    secret_store: SecretStore
    config_path: Path
    config_error: str | None = None


class BootstrapError(RuntimeError):
    """Kernel failed to start; message is user-facing."""


async def seed_role_mappings(kernel: KairoKernel, document: ConfigDocument) -> list[str]:
    """Seed chat-role routing from the document via the public providers facade.

    ProviderRoleMapping is not publicly constructible (private kernel service),
    so roles are applied after start through ``providers.map_role`` with the
    catalog revision read from ``providers.snapshot()`` (increments per call).
    """
    snapshot = await kernel.providers.snapshot()
    expected = int(getattr(snapshot, "revision", 0) or 0)
    applied: list[str] = []
    for mapping in document.roles:
        result: KernelResult[object] = cast(
            KernelResult[object], await kernel.providers.map_role(mapping.role, mapping.profile_id, expected)
        )
        if result.ok:
            applied.append(mapping.role)
            expected += 1
    return applied


async def _build(
    options: BootstrapOptions,
    secret_store: SecretStore,
    provider: object | None = None,
    tools: object | None = None,
) -> BootstrapResult:
    path = resolve_config_path(options.config_path)
    adapter = ConfigDocumentAdapter(path, safe_mode=options.safe_mode)
    document = adapter.load()
    if options.theme:
        document = replace(document, theme=options.theme)
    workspace_root = str(Path(options.workspace_root).expanduser().resolve())
    # The kernel fail-closes on a trust store inside the workspace (the default
    # ``.kairo/trust`` is), so the skill trust store lives in the per-user data
    # dir alongside the global config document. Safe mode is unaffected: trust
    # reads stay available and the store is only written by an explicit
    # ``skills.trust()`` user action.
    trust_directory = default_trust_dir()
    trust_directory.mkdir(parents=True, exist_ok=True)
    config = KernelConfig(
        workspace_root,
        database_path=".kairo/kernel.db",
        profiles=tuple(document.profiles),
        default_profile_id=document.default_profile_id,
        connect_mcp_on_start=False,
        trust_directory=str(trust_directory),
    )
    kernel = build_kernel(
        config,
        KernelDependencies(
            provider=cast(ProviderPort | None, provider),
            tools=cast(ToolRegistryPort | None, tools),
            secrets=KeyringSecretPort(secret_store),
        ),
    )
    result = await kernel.start()
    if result.error is not None:
        raise BootstrapError(result.error.message)
    await seed_role_mappings(kernel, document)
    status = await kernel.status()
    setup_complete = not document.is_empty
    store = AppStore(
        AppState(
            kernel_status=status,
            document=document,
            setup_complete=setup_complete,
            page=PageId.CHAT if setup_complete else PageId.SETUP,
            safe_mode=options.safe_mode,
            reduced_motion=options.reduced_motion,
            workspace_root=workspace_root,
        )
    )
    return BootstrapResult(kernel, document, store, secret_store, path, adapter.last_error)


def build_running_kernel(
    options: BootstrapOptions,
    *,
    secret_store: SecretStore | None = None,
    provider: object | None = None,
    tools: object | None = None,
) -> BootstrapResult:
    """Synchronous entry: boot the kernel and return the app's dependencies.

    ``provider`` and ``tools`` are test seams for injecting public port fakes
    (ProviderPort / ToolRegistryPort); in production they stay None and the
    factory composes the real router and builtin/MCP registries.
    """
    store = secret_store or make_secret_store(safe_mode=options.safe_mode)
    return asyncio.run(_build(options, store, provider, tools))
