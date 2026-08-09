"""KernelGate: kernel.capabilities() reflects the real composition."""

from __future__ import annotations

import asyncio

from kairo_kernel import KernelConfig, build_kernel
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.lifecycle import KernelCapabilities
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.mcp import McpServerConfig

PROFILE = ProviderProfile(
    ProfileId("openai/default"), "OpenAI", "openai", "gpt-5", "https://api.openai.com", 128000, 8192, 0.2
)


def _config(
    tmp_path: object,
    *,
    profiles: tuple[ProviderProfile, ...] = (),
    mcp_servers: tuple[McpServerConfig, ...] = (),
) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(
        root,
        database_path=str(root + "/kernel.db"),
        enable_builtin_tools=False,
        profiles=profiles,
        mcp_servers=mcp_servers,
    )


def test_capabilities_reflect_composition(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = build_kernel(_config(tmp_path))
        async with kernel:
            capabilities = await kernel.capabilities()
            # No providers or MCP servers are configured by default.
            assert "providers" not in capabilities.features
            assert "mcp" not in capabilities.features
            assert any(note.startswith("providers:") for note in capabilities.limitations)
            assert any(note.startswith("mcp:") for note in capabilities.limitations)
            # Baseline capabilities are always present.
            assert {"turns", "sessions", "preferences", "commands"} <= set(capabilities.features)

    asyncio.run(exercise())


def test_capabilities_include_configured_integrations(tmp_path: object) -> None:
    async def exercise() -> None:
        kernel = build_kernel(
            _config(
                tmp_path,
                profiles=(PROFILE,),
                mcp_servers=(McpServerConfig("srv", "stdio", command="x"),),
            )
        )
        async with kernel:
            capabilities = await kernel.capabilities()
            assert {"providers", "mcp"} <= set(capabilities.features)

    asyncio.run(exercise())


def test_capabilities_contract_round_trip() -> None:
    capabilities = KernelCapabilities(features=("turns",), limitations=("mcp: none",))
    decoded = KernelCapabilities.from_json(capabilities.to_json())
    assert decoded == capabilities
