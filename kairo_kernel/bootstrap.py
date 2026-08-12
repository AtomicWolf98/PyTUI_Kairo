"""Public one-call kernel bootstrap: config document -> running KairoKernel.

This module is the only supported way for frontends to open a kernel. It
loads the global configuration document, wires the provider catalog
repository (persistent, or in-memory in safe mode), composes the kernel and
starts it. Frontends must not import ``kairo_kernel.services`` or re-implement
any of this composition; secret values are never read or logged here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairo_kernel._version import __version__
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.factory import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.kernel import KairoKernel
from kairo_kernel.ports.providers import ProviderPort
from kairo_kernel.ports.services import SecretPort
from kairo_kernel.ports.tools import ToolRegistryPort
from kairo_kernel.services.config_document import (
    DocumentProviderCatalog,
    KernelConfigDocument,
    KernelConfigStore,
)
from kairo_kernel.services.providers import (
    InMemoryProviderCatalog,
    ProviderCatalogRepository,
    ProviderCatalogSnapshot,
)

SAFE_MODE_WARNING = (
    "Safe mode is active: provider catalog changes are not persisted "
    "and MCP servers are not connected."
)


@dataclass(frozen=True)
class KernelOpenOptions:
    """Everything a frontend must decide to open a kernel."""

    workspace_root: str
    config_path: str
    safe_mode: bool = False
    package_version: str | None = None


@dataclass(frozen=True)
class OpenedKernel:
    """A started kernel plus the configuration document it was opened from."""

    kernel: KairoKernel
    config_revision: int
    config_missing: bool
    config_warning: str | None


async def open_kernel(
    options: KernelOpenOptions,
    *,
    secrets: SecretPort | None = None,
    provider: ProviderPort | None = None,
    tools: ToolRegistryPort | None = None,
) -> KernelResult[OpenedKernel]:
    """Load the config document, compose and start the kernel.

    - A missing document starts an empty kernel; ``config_missing`` is True
      and this is not an error.
    - An invalid document returns a typed failure and is never overwritten.
    - Safe mode keeps provider mutations in memory, never auto-connects MCP
      and does not relax authorization.
    - A failed ``kernel.start()`` shuts down every opened resource (including
      the database) before the failure is returned.
    """
    store = KernelConfigStore(Path(options.config_path).expanduser())
    loaded = await store.load()
    if loaded.error is not None:
        if loaded.error.code is ErrorCode.NOT_FOUND:
            document = KernelConfigDocument()
            config_missing = True
        else:
            return KernelResult.failure(loaded.error)
    else:
        assert loaded.value is not None
        document = loaded.value
        config_missing = False

    workspace_root = str(Path(options.workspace_root).expanduser().resolve())
    try:
        config = KernelConfig(
            workspace_root,
            database_path=".kairo/kernel.db",
            package_version=options.package_version or __version__,
            profiles=document.profiles,
            provider_roles=document.roles,
            default_profile_id=document.default_profile_id,
            mcp_servers=document.mcp_servers,
            connect_mcp_on_start=False,
        )
    except ValueError as exc:
        return KernelResult.failure(
            KernelError(ErrorCode.INVALID_ARGUMENT, str(exc), operation="kernel.open")
        )

    if options.safe_mode:
        catalog_repository: ProviderCatalogRepository = InMemoryProviderCatalog(
            ProviderCatalogSnapshot(0, document.profiles, document.roles)
        )
        config_warning = SAFE_MODE_WARNING
    else:
        catalog_repository = DocumentProviderCatalog(store)
        config_warning = None

    kernel = build_kernel(
        config,
        KernelDependencies(
            provider_catalog=catalog_repository,
            secrets=secrets,
            provider=provider,
            tools=tools,
        ),
    )
    result = await kernel.start()
    if result.error is not None:
        await kernel.shutdown()
        return KernelResult.failure(result.error)
    return KernelResult.success(
        OpenedKernel(kernel, document.revision, config_missing, config_warning)
    )
