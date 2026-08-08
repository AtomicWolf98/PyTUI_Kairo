from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SecretDescriptor, SecretInput
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.services import SecretPort
from kairo_kernel.services.providers import (
    InMemoryProviderCatalog,
    ProviderProbePort,
    ProviderService,
    SecretRef,
    profile_with_secret,
)


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[SecretId, str] = {}

    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
        return KernelResult.success(
            SecretDescriptor(secret_id, "test", "***", secret_id in self.values)
        )

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
        value = self.values.get(secret_id)
        if value is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing secret"))
        return KernelResult.success(value)

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
        self.values[secret.secret_id] = secret.value
        return KernelResult.success(SecretDescriptor(secret.secret_id, "test", "***", True))

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
        return KernelResult.success(self.values.pop(secret_id, None) is not None)


class FakeProbe(ProviderProbePort):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[ProviderProfile] = []

    async def probe(self, profile: ProviderProfile, secrets: SecretPort) -> KernelResult[ProviderProfile]:
        self.calls.append(profile)
        if profile.secret_id:
            resolved = await secrets.resolve(SecretId(profile.secret_id))
            assert resolved.ok and resolved.value is not None
            if self.fail:
                return KernelResult.failure(
                    KernelError(ErrorCode.PROVIDER_AUTH, f"provider rejected {resolved.value}")
                )
        return KernelResult.success(profile)


def profile(identifier: str, model: str = "model") -> ProviderProfile:
    return ProviderProfile(
        ProfileId(identifier),
        identifier,
        "openai_chat",
        model,
        "https://example.test/v1",
        32_000,
        2_000,
        0.2,
    )


async def make_service(*, failing_probe: bool = False) -> tuple[ProviderService, FakeSecrets, FakeProbe]:
    secrets = FakeSecrets()
    probe = FakeProbe(fail=failing_probe)
    opened = await ProviderService.open(
        InMemoryProviderCatalog(),
        secrets,
        (("openai_chat", probe),),
    )
    assert opened.ok and opened.value is not None
    return opened.value, secrets, probe


async def test_profile_crud_role_mapping_and_snapshot_resolution() -> None:
    service, _, _ = await make_service()
    created = await service.create_profile(profile("chat"), expected_revision=0)
    assert created.ok and created.value is not None
    old_snapshot = created.value
    mapped = await service.map_role("chat", ProfileId("chat"), expected_revision=1)
    assert mapped.ok and mapped.value is not None

    updated_profile = profile("chat", "model-v2")
    updated = await service.update_profile(updated_profile, expected_revision=2)
    assert updated.ok and updated.value is not None
    old_resolved = await service.resolve_profile(ProfileId("chat"), snapshot=old_snapshot)
    current_resolved = await service.resolve_profile(role="chat")
    assert old_resolved.ok and old_resolved.value is not None and old_resolved.value.model == "model"
    assert current_resolved.ok and current_resolved.value is not None and current_resolved.value.model == "model-v2"

    referenced = await service.delete_profile(ProfileId("chat"), expected_revision=3)
    assert not referenced.ok and referenced.error is not None
    assert referenced.error.code is ErrorCode.CONFLICT
    unmapped = await service.unmap_role("chat", expected_revision=3)
    assert unmapped.ok
    deleted = await service.delete_profile(ProfileId("chat"), expected_revision=4)
    assert deleted.ok and deleted.value is not None and deleted.value.revision == 5


async def test_secret_ref_state_probe_and_errors_never_expose_secret() -> None:
    service, _, probe = await make_service(failing_probe=True)
    stored = await service.store_secret(SecretInput(SecretId("provider-key"), "super-secret-value"))
    assert stored.ok and stored.value == SecretRef(SecretId("provider-key"))
    secured = profile_with_secret(profile("secured"), stored.value)
    created = await service.create_profile(secured, expected_revision=0)
    assert created.ok and created.value is not None
    assert "super-secret-value" not in repr(created.value)
    assert "super-secret-value" not in secured.to_json()

    result = await service.probe(ProfileId("secured"))
    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.PROVIDER_AUTH
    assert result.error.message == "Provider probe failed."
    assert "super-secret-value" not in result.error.to_json()
    assert probe.calls == [secured]

    referenced = await service.delete_secret(SecretRef(SecretId("provider-key")))
    assert not referenced.ok and referenced.error is not None
    assert referenced.error.code is ErrorCode.CONFLICT


async def test_probe_success_and_concurrent_expected_revision_conflict() -> None:
    service, _, _ = await make_service()
    first, second = await asyncio.gather(
        service.create_profile(profile("one"), expected_revision=0),
        service.create_profile(profile("two"), expected_revision=0),
    )
    assert sum((first.ok, second.ok)) == 1
    failure = second if first.ok else first
    assert failure.error is not None and failure.error.code is ErrorCode.CONFLICT

    selected = first.value if first.ok else second.value
    assert selected is not None
    profile_id = selected.profiles[0].profile_id
    probed = await service.probe(profile_id)
    assert probed.ok and probed.value is not None and probed.value.reachable


async def test_invalid_profile_and_missing_secret_reference_are_rejected() -> None:
    service, _, _ = await make_service()
    invalid = profile_with_secret(profile("bad"), SecretRef(SecretId("missing")))
    missing = await service.create_profile(invalid, expected_revision=0)
    assert not missing.ok and missing.error is not None
    assert missing.error.code is ErrorCode.NOT_FOUND

    malformed = ProviderProfile(
        ProfileId("bad-url"),
        "bad",
        "openai_chat",
        "model",
        "file:///tmp/secret",
        10,
        20,
        0.2,
    )
    rejected = await service.create_profile(malformed, expected_revision=0)
    assert not rejected.ok and rejected.error is not None
    assert rejected.error.code is ErrorCode.CONFIG_INVALID
