"""Public one-call kernel bootstrap acceptance tests (work order K0)."""

from __future__ import annotations

import asyncio
import json
import os

import kairo_kernel
from kairo_kernel import KernelOpenOptions, OpenedKernel, open_kernel
from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.mcp import McpServerConfig
from kairo_kernel.services.config_document import (
    KernelConfigDocument,
    KernelConfigStore,
    document_to_json,
)
from kairo_kernel.services.providers import ProviderRoleMapping


def _profile(profile_id: str = "openai_responses:gpt-4o") -> ProviderProfile:
    return ProviderProfile(
        ProfileId(profile_id),
        "OpenAI Responses",
        "openai_responses",
        "gpt-4o",
        "https://api.openai.com/v1",
        128_000,
        16_384,
        0.7,
    )


def _options(tmp_path: object, **overrides: object) -> KernelOpenOptions:
    return KernelOpenOptions(
        workspace_root=str(tmp_path),
        config_path=str(tmp_path) + "/config.json",
        **overrides,
    )


def _write_document(tmp_path: object, document: KernelConfigDocument) -> str:
    path = str(tmp_path) + "/config.json"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document_to_json(document), ensure_ascii=False))
    return path


def test_open_missing_document_starts_empty_kernel(tmp_path: object) -> None:
    async def exercise() -> None:
        result = await open_kernel(_options(tmp_path))
        assert result.ok and result.value is not None
        opened = result.value
        assert isinstance(opened, OpenedKernel)
        assert opened.config_missing is True
        assert opened.config_revision == 0
        assert opened.config_warning is None
        assert opened.kernel.state.value == "running"
        snapshot = await opened.kernel.providers.snapshot()
        assert snapshot.profiles == ()
        status = await opened.kernel.status()
        assert status.workspace_root == str(tmp_path)
        await opened.kernel.shutdown()

    asyncio.run(exercise())


def test_open_restores_profiles_roles_and_default(tmp_path: object) -> None:
    async def exercise() -> None:
        profile = _profile()
        document = KernelConfigDocument(
            profiles=(profile,),
            roles=(ProviderRoleMapping("chat", profile.profile_id),),
            default_profile_id=profile.profile_id,
            revision=3,
        )
        _write_document(tmp_path, document)
        result = await open_kernel(_options(tmp_path))
        assert result.ok and result.value is not None
        opened = result.value
        assert opened.config_missing is False
        assert opened.config_revision == 3
        snapshot = await opened.kernel.providers.snapshot()
        assert snapshot.profiles == (profile,)
        assert snapshot.roles == (ProviderRoleMapping("chat", profile.profile_id),)
        status = await opened.kernel.status()
        assert status.active_profile_id == profile.profile_id
        resolved = await opened.kernel.providers.resolve(role="chat")
        assert resolved.ok and resolved.value == profile
        await opened.kernel.shutdown()

    asyncio.run(exercise())


def test_open_invalid_document_fails_without_overwrite(tmp_path: object) -> None:
    async def exercise() -> None:
        path = str(tmp_path) + "/config.json"
        broken = '{"version": 1, "profiles": [{"profile_id": "p1",'
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(broken)
        result = await open_kernel(_options(tmp_path))
        assert result.error is not None
        assert result.error.code is ErrorCode.CONFIG_INVALID
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == broken

    asyncio.run(exercise())


def test_provider_create_persists_to_document(tmp_path: object) -> None:
    async def exercise() -> None:
        result = await open_kernel(_options(tmp_path))
        assert result.ok and result.value is not None
        opened = result.value
        created = await opened.kernel.providers.create_profile(_profile(), 0)
        assert created.ok
        await opened.kernel.shutdown()

        store = KernelConfigStore(str(tmp_path) + "/config.json")
        loaded = await store.load()
        assert loaded.ok and loaded.value is not None
        assert loaded.value.profiles == (_profile(),)
        assert loaded.value.revision == 1

    asyncio.run(exercise())


def test_role_mapping_persists_to_document(tmp_path: object) -> None:
    async def exercise() -> None:
        result = await open_kernel(_options(tmp_path))
        assert result.ok and result.value is not None
        opened = result.value
        profile = _profile()
        created = await opened.kernel.providers.create_profile(profile, 0)
        assert created.ok
        mapped = await opened.kernel.providers.map_role("chat", profile.profile_id, 1)
        assert mapped.ok
        await opened.kernel.shutdown()

        store = KernelConfigStore(str(tmp_path) + "/config.json")
        loaded = await store.load()
        assert loaded.ok and loaded.value is not None
        assert loaded.value.roles == (ProviderRoleMapping("chat", profile.profile_id),)

    asyncio.run(exercise())


def test_second_open_restores_first_open_mutations(tmp_path: object) -> None:
    async def exercise() -> None:
        first = await open_kernel(_options(tmp_path))
        assert first.ok and first.value is not None
        profile = _profile()
        assert (await first.value.kernel.providers.create_profile(profile, 0)).ok
        assert (await first.value.kernel.providers.map_role("chat", profile.profile_id, 1)).ok
        await first.value.kernel.shutdown()

        second = await open_kernel(_options(tmp_path))
        assert second.ok and second.value is not None
        snapshot = await second.value.kernel.providers.snapshot()
        assert snapshot.profiles == (profile,)
        assert snapshot.roles == (ProviderRoleMapping("chat", profile.profile_id),)
        resolved = await second.value.kernel.providers.resolve(role="chat")
        assert resolved.ok and resolved.value == profile
        await second.value.kernel.shutdown()

    asyncio.run(exercise())


def test_safe_mode_does_not_connect_mcp(tmp_path: object) -> None:
    async def exercise() -> None:
        document = KernelConfigDocument(
            mcp_servers=(
                McpServerConfig("broken-server", "stdio", command="__no_such_kairo_executable__"),
            ),
        )
        _write_document(tmp_path, document)
        result = await open_kernel(_options(tmp_path, safe_mode=True))
        assert result.ok and result.value is not None
        opened = result.value
        assert opened.config_warning is not None and "Safe mode" in opened.config_warning
        assert opened.kernel.state.value == "running"
        assert opened.kernel.mcp.catalog() == ()
        status = await opened.kernel.status()
        assert status.state.value == "running"
        await opened.kernel.shutdown()

    asyncio.run(exercise())


def test_bootstrap_error_redacts_secret_markers(tmp_path: object) -> None:
    async def exercise() -> None:
        secret_marker = "sk-abc123secret"
        broken = (
            '{"version": 1, "profiles": [{"profile_id": "p1", '
            f'"label": "{secret_marker}"'
            '}], "broken": '
        )
        with open(str(tmp_path) + "/config.json", "w", encoding="utf-8") as handle:
            handle.write(broken)
        result = await open_kernel(_options(tmp_path))
        assert result.error is not None
        rendered = result.error.to_json()
        assert secret_marker not in rendered
        assert secret_marker not in repr(result.error)
        assert secret_marker not in result.error.message

    asyncio.run(exercise())


def test_public_root_exports_open_kernel_types() -> None:
    assert {"KernelOpenOptions", "OpenedKernel", "open_kernel"} <= set(kairo_kernel.__all__)
    assert kairo_kernel.KernelOpenOptions is KernelOpenOptions
    assert kairo_kernel.OpenedKernel is OpenedKernel
    assert kairo_kernel.open_kernel is open_kernel


def test_open_failure_closes_database(tmp_path: object) -> None:
    async def exercise() -> None:
        # A role mapping that references a missing profile fails validation
        # during kernel.start(); the failure path must not leak resources or
        # overwrite the document.
        document = KernelConfigDocument(
            roles=(ProviderRoleMapping("chat", ProfileId("missing-profile")),),
        )
        path = _write_document(tmp_path, document)
        result = await open_kernel(_options(tmp_path))
        assert result.error is not None
        assert result.error.code is ErrorCode.CONFIG_INVALID
        # The kernel never created its database directory and no temporary
        # config artifacts were left behind.
        assert ".kairo" not in os.listdir(str(tmp_path))
        leftovers = [name for name in os.listdir(str(tmp_path)) if name.startswith("config.json")]
        assert leftovers == ["config.json"]
        with open(path, encoding="utf-8") as handle:
            assert json.loads(handle.read()) == document_to_json(document)

    asyncio.run(exercise())
