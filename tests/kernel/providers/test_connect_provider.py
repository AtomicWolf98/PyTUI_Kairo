"""Atomic provider connection use case acceptance tests (work order K1)."""

from __future__ import annotations

import asyncio

from kairo_kernel import KernelConfig, KernelDependencies, KernelOpenOptions, build_kernel, open_kernel
from kairo_kernel.contracts.enums import ErrorCode, EventType
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.providers import (
    ProviderConnectionReceipt,
    ProviderConnectionRequest,
    ProviderProfile,
)
from kairo_kernel.contracts.support import SecretDescriptor, SecretInput
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.services.providers import ProviderCatalogSnapshot, ProviderRoleMapping

SECRET_VALUE = "sk-secret-value-12345"
SECRET_ID = "openai_responses:gpt-4o"
PROFILE_ID = "openai_responses:gpt-4o"


def _profile(profile_id: str = PROFILE_ID, secret_id: str = SECRET_ID) -> ProviderProfile:
    return ProviderProfile(
        ProfileId(profile_id),
        "OpenAI Responses",
        "openai_responses",
        "gpt-4o",
        "https://api.openai.com/v1",
        128_000,
        16_384,
        0.7,
        secret_id=secret_id,
    )


def _request(**overrides: object) -> ProviderConnectionRequest:
    secret = overrides.pop("secret", SecretInput(SecretId(SECRET_ID), SECRET_VALUE))
    profile = overrides.pop("profile", _profile())
    return ProviderConnectionRequest(
        profile,
        secret=secret,
        **overrides,
    )


def _config(tmp_path: object) -> KernelConfig:
    root = str(tmp_path)
    return KernelConfig(root, database_path=root + "/kernel.db", enable_builtin_tools=False)


class TrackingSecrets:
    """In-memory SecretPort recording every store/delete call."""

    def __init__(
        self,
        *,
        fail_store: bool = False,
        fail_delete: bool = False,
        present: bool = False,
    ) -> None:
        self.fail_store = fail_store
        self.fail_delete = fail_delete
        self.present = present
        self.stored: list[SecretInput] = []
        self.deleted: list[SecretId] = []
        self._values: dict[SecretId, str] = {}

    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
        found = secret_id in self._values or self.present
        return KernelResult.success(
            SecretDescriptor(secret_id, "fake", "********" if found else "", found)
        )

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
        value = self._values.get(secret_id)
        if value is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, "Secret was not found.", operation="secret.resolve")
            )
        return KernelResult.success(value)

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
        if self.fail_store:
            return KernelResult.failure(
                KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "Secret store failed.", operation="secret.store")
            )
        self._values[secret.secret_id] = secret.value
        self.stored.append(secret)
        return KernelResult.success(SecretDescriptor(secret.secret_id, "fake", "********", True))

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
        if self.fail_delete:
            return KernelResult.failure(
                KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "Secret delete failed.", operation="secret.delete")
            )
        self.deleted.append(secret_id)
        return KernelResult.success(self._values.pop(secret_id, None) is not None)


class FailingCatalog:
    """ProviderCatalogRepository whose persistence can be forced to fail."""

    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.snapshot = ProviderCatalogSnapshot(0)
        self.saved_with_default: list[tuple[ProviderCatalogSnapshot, ProfileId | None]] = []

    async def load(self) -> KernelResult[ProviderCatalogSnapshot]:
        return KernelResult.success(self.snapshot)

    async def save(self, snapshot: ProviderCatalogSnapshot) -> KernelResult[ProviderCatalogSnapshot]:
        if self.fail_save:
            return KernelResult.failure(
                KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "Catalog save failed.", operation="provider.save")
            )
        self.snapshot = snapshot
        return KernelResult.success(snapshot)

    async def save_with_default(
        self,
        snapshot: ProviderCatalogSnapshot,
        default_profile_id: ProfileId | None,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        if self.fail_save:
            return KernelResult.failure(
                KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "Catalog save failed.", operation="provider.save")
            )
        self.saved_with_default.append((snapshot, default_profile_id))
        self.snapshot = snapshot
        return KernelResult.success(snapshot)


def test_connect_new_profile_secret_role_default_succeeds(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        async with kernel:
            receipt = await kernel.providers.configure(_request())
            assert receipt.ok and receipt.value is not None
            assert isinstance(receipt.value, ProviderConnectionReceipt)
            assert receipt.value.profile_id == ProfileId(PROFILE_ID)
            assert receipt.value.role == "chat"
            assert receipt.value.catalog_revision == 1
            assert receipt.value.default_profile_id == ProfileId(PROFILE_ID)

            snapshot = await kernel.providers.snapshot()
            assert snapshot.revision == 1
            assert snapshot.profiles == (_profile(),)
            assert snapshot.roles == (ProviderRoleMapping("chat", ProfileId(PROFILE_ID)),)
            resolved = await kernel.providers.resolve(role="chat")
            assert resolved.ok and resolved.value == _profile()
            assert len(secrets.stored) == 1
            assert secrets.stored[0].secret_id == SecretId(SECRET_ID)

    asyncio.run(exercise())


def test_connect_profile_without_secret_succeeds(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        async with kernel:
            request = _request(secret=None)
            receipt = await kernel.providers.configure(request)
            assert receipt.ok and receipt.value is not None
            assert receipt.value.profile_id == ProfileId(PROFILE_ID)
            assert secrets.stored == []

    asyncio.run(exercise())


def test_expected_revision_conflict_zero_side_effects(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        catalog = FailingCatalog()
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(secrets=secrets, provider_catalog=catalog),
        )
        async with kernel:
            receipt = await kernel.providers.configure(_request(expected_revision=99))
            assert receipt.error is not None
            assert receipt.error.code is ErrorCode.CONFLICT
            assert secrets.stored == []
            assert catalog.saved_with_default == []
            assert (await kernel.providers.snapshot()).revision == 0

    asyncio.run(exercise())


def test_secret_store_failure_zero_side_effects(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets(fail_store=True)
        catalog = FailingCatalog()
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(secrets=secrets, provider_catalog=catalog),
        )
        async with kernel:
            receipt = await kernel.providers.configure(_request())
            assert receipt.error is not None
            assert receipt.error.code is ErrorCode.CONFIG_PERSISTENCE_FAILED
            assert secrets.deleted == []
            assert catalog.saved_with_default == []
            assert (await kernel.providers.snapshot()).profiles == ()

    asyncio.run(exercise())


def test_document_save_failure_deletes_new_secret(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        catalog = FailingCatalog(fail_save=True)
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(secrets=secrets, provider_catalog=catalog),
        )
        async with kernel:
            receipt = await kernel.providers.configure(_request())
            assert receipt.error is not None
            assert receipt.error.code is ErrorCode.CONFIG_PERSISTENCE_FAILED
            # Compensation: the freshly created secret is removed.
            assert secrets.deleted == [SecretId(SECRET_ID)]
            assert (await kernel.providers.snapshot()).profiles == ()

    asyncio.run(exercise())


def test_live_snapshot_swaps_only_after_persistence(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        catalog = FailingCatalog(fail_save=True)
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(secrets=secrets, provider_catalog=catalog),
        )
        async with kernel:
            failed = await kernel.providers.configure(_request())
            assert failed.error is not None
            assert (await kernel.providers.snapshot()).profiles == ()
            # Persistence succeeds now; only then does the live snapshot swap.
            catalog.fail_save = False
            succeeded = await kernel.providers.configure(_request())
            assert succeeded.ok
            assert (await kernel.providers.snapshot()).profiles == (_profile(),)

    asyncio.run(exercise())


def test_compensation_failure_marks_kernel_degraded(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets(fail_delete=True)
        catalog = FailingCatalog(fail_save=True)
        kernel = build_kernel(
            _config(tmp_path),
            KernelDependencies(secrets=secrets, provider_catalog=catalog),
        )
        async with kernel:
            receipt = await kernel.providers.configure(_request())
            assert receipt.error is not None
            assert kernel.state.value == "degraded"
            # Subsequent mutations are rejected while degraded.
            rejected = await kernel.providers.configure(_request())
            assert rejected.error is not None
            assert rejected.error.code is ErrorCode.KERNEL_DEGRADED

    asyncio.run(exercise())


def test_restart_restores_profile_role_default(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        options = KernelOpenOptions(
            workspace_root=str(tmp_path),
            config_path=str(tmp_path) + "/config.json",
        )
        first = await open_kernel(options, secrets=secrets)
        assert first.ok and first.value is not None
        receipt = await first.value.kernel.providers.configure(_request())
        assert receipt.ok
        await first.value.kernel.shutdown()

        second = await open_kernel(options)
        assert second.ok and second.value is not None
        snapshot = await second.value.kernel.providers.snapshot()
        assert snapshot.profiles == (_profile(),)
        assert snapshot.roles == (ProviderRoleMapping("chat", ProfileId(PROFILE_ID)),)
        resolved = await second.value.kernel.providers.resolve(role="chat")
        assert resolved.ok and resolved.value == _profile()
        status = await second.value.kernel.status()
        assert status.active_profile_id == ProfileId(PROFILE_ID)
        await second.value.kernel.shutdown()

    asyncio.run(exercise())


def test_configure_emits_exactly_one_change_event(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        async with kernel:
            subscription = await kernel.events.subscribe((await kernel.events.snapshot()).newest_sequence)
            receipt = await kernel.providers.configure(_request())
            assert receipt.ok
            event = await asyncio.wait_for(subscription.receive(), 1)
            assert event.event_type is EventType.PROVIDER_CHANGED
            await subscription.close()

    asyncio.run(exercise())


def test_request_repr_error_and_events_have_no_secret_marker(tmp_path: object) -> None:
    async def exercise() -> None:
        request = _request()
        assert SECRET_VALUE not in repr(request)
        assert SECRET_VALUE not in repr(request.secret)

        secrets = TrackingSecrets(fail_store=True)
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        async with kernel:
            failed = await kernel.providers.configure(request)
            assert failed.error is not None
            assert SECRET_VALUE not in failed.error.message
            assert SECRET_VALUE not in failed.error.to_json()
            assert SECRET_VALUE not in repr(failed.error)

    asyncio.run(exercise())


def test_busy_closing_degraded_gate(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        request = _request()
        before_start = await kernel.providers.configure(request)
        assert before_start.error is not None
        assert before_start.error.code is ErrorCode.KERNEL_NOT_RUNNING

        await kernel.start()
        await kernel.mark_degraded("forced for gate test")
        degraded = await kernel.providers.configure(request)
        assert degraded.error is not None
        assert degraded.error.code is ErrorCode.KERNEL_DEGRADED

    asyncio.run(exercise())


def test_invalid_role_profile_secret_reference_typed_failure(tmp_path: object) -> None:
    async def exercise() -> None:
        secrets = TrackingSecrets()
        kernel = build_kernel(_config(tmp_path), KernelDependencies(secrets=secrets))
        async with kernel:
            empty_role = await kernel.providers.configure(_request(role="  "))
            assert empty_role.error is not None
            assert empty_role.error.code is ErrorCode.INVALID_ARGUMENT

            mismatched = _request(secret=SecretInput(SecretId("other:model"), SECRET_VALUE))
            mismatch = await kernel.providers.configure(mismatched)
            assert mismatch.error is not None
            assert mismatch.error.code is ErrorCode.INVALID_ARGUMENT

            invalid_profile = ProviderProfile(
                ProfileId("openai_responses:gpt-4o"),
                "OpenAI",
                "openai_responses",
                "gpt-4o",
                "not-a-url",
                128_000,
                16_384,
                0.7,
            )
            bad_url = await kernel.providers.configure(_request(profile=invalid_profile, secret=None))
            assert bad_url.error is not None
            assert bad_url.error.code is ErrorCode.CONFIG_INVALID

            duplicate = await kernel.providers.configure(_request())
            assert duplicate.ok
            again = await kernel.providers.configure(_request())
            assert again.error is not None
            assert again.error.code is ErrorCode.CONFLICT

    asyncio.run(exercise())
