"""TUI test fixtures: a real kernel in a tmp workspace."""

from __future__ import annotations

from pathlib import Path

import pytest
from kairo_kernel import KairoKernel, KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.ports import SecretPort
from kairo_kernel.ports.tools import ToolRegistryPort

from kairo_tui.keyring_store import KeyringSecretPort, SecretStore
from tests.support.fakes import FakeProvider


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def fake_secret_port() -> SecretPort:
    return KeyringSecretPort(SecretStore(_MemoryBackend()))


class _MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


@pytest.fixture
def kernel_factory(workspace: Path, fake_secret_port: SecretPort):
    """Build a real KairoKernel wired to public ports; no network, no private imports."""

    def make(
        *,
        provider: FakeProvider | None = None,
        secrets: SecretPort | None = None,
        event_queue_size: int = 256,
        profiles: tuple[ProviderProfile, ...] = (),
        tools: ToolRegistryPort | None = None,
    ) -> KairoKernel:
        config = KernelConfig(
            str(workspace),
            database_path="kernel.db",
            enable_builtin_tools=False,
            event_queue_size=event_queue_size,
            profiles=profiles,
        )
        return build_kernel(
            config,
            KernelDependencies(provider=provider or FakeProvider(), secrets=secrets or fake_secret_port, tools=tools),
        )

    return make
