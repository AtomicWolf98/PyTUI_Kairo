"""Provider profile catalog, role routing and secret references."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import urlparse

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SecretInput
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.services import SecretPort


@dataclass(frozen=True)
class SecretRef:
    secret_id: SecretId


@dataclass(frozen=True)
class ProviderRoleMapping:
    role: str
    profile_id: ProfileId


@dataclass(frozen=True)
class ProviderCatalogSnapshot:
    revision: int
    profiles: tuple[ProviderProfile, ...] = ()
    roles: tuple[ProviderRoleMapping, ...] = ()


@dataclass(frozen=True)
class ProviderProbeResult:
    profile_id: ProfileId
    provider: str
    model: str
    reachable: bool


class ProviderCatalogRepository(Protocol):
    async def load(self) -> KernelResult[ProviderCatalogSnapshot]: ...

    async def save(self, snapshot: ProviderCatalogSnapshot) -> KernelResult[ProviderCatalogSnapshot]: ...


class ProviderProbePort(Protocol):
    async def probe(self, profile: ProviderProfile, secrets: SecretPort) -> KernelResult[ProviderProfile]: ...


class InMemoryProviderCatalog:
    def __init__(self, initial: ProviderCatalogSnapshot = ProviderCatalogSnapshot(0)) -> None:
        self._snapshot = initial

    async def load(self) -> KernelResult[ProviderCatalogSnapshot]:
        return KernelResult.success(self._snapshot)

    async def save(self, snapshot: ProviderCatalogSnapshot) -> KernelResult[ProviderCatalogSnapshot]:
        self._snapshot = snapshot
        return KernelResult.success(snapshot)


class ProviderService:
    """Manage immutable provider profiles without exposing secret values."""

    def __init__(
        self,
        repository: ProviderCatalogRepository,
        secrets: SecretPort,
        probes: tuple[tuple[str, ProviderProbePort], ...],
        initial: ProviderCatalogSnapshot,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._probes = dict(probes)
        self._snapshot = initial
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        repository: ProviderCatalogRepository,
        secrets: SecretPort,
        probes: tuple[tuple[str, ProviderProbePort], ...] = (),
    ) -> KernelResult[ProviderService]:
        loaded = await repository.load()
        if not loaded.ok:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    "Provider catalog could not be loaded.",
                    retryable=loaded.error.retryable if loaded.error is not None else False,
                    operation="provider.open",
                )
            )
        assert loaded.value is not None
        service = cls(repository, secrets, probes, loaded.value)
        validation = _validate_catalog(loaded.value)
        if validation is not None:
            return KernelResult.failure(validation)
        return KernelResult.success(service)

    async def load_from_repository(self) -> KernelResult[ProviderCatalogSnapshot]:
        """Reload the persisted catalog, validating before swapping the live snapshot."""

        loaded = await self._repository.load()
        if not loaded.ok or loaded.value is None:
            error = loaded.error
            return KernelResult.failure(
                KernelError(
                    error.code if error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    error.message if error is not None else "Provider catalog could not be loaded.",
                    error.retryable if error is not None else False,
                    "provider.open",
                )
            )
        validation = _validate_catalog(loaded.value)
        if validation is not None:
            return KernelResult.failure(validation)
        async with self._lock:
            self._snapshot = loaded.value
        return KernelResult.success(loaded.value)

    async def snapshot(self) -> ProviderCatalogSnapshot:
        async with self._lock:
            return self._snapshot

    def register_probe(self, provider: str, probe: ProviderProbePort) -> None:
        """Attach a probe for a provider kind after construction (composition root use)."""

        self._probes[provider] = probe

    async def create_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        validation = _validate_profile(profile)
        if validation is not None:
            return KernelResult.failure(validation)
        async with self._lock:
            conflict = self._conflict(expected_revision, "provider.create")
            if conflict is not None:
                return conflict
            secret_check = await self._check_secret_ref(profile)
            if secret_check is not None:
                return KernelResult.failure(secret_check)
            if any(item.profile_id == profile.profile_id for item in self._snapshot.profiles):
                return _provider_failure(ErrorCode.CONFLICT, "Provider profile already exists.", "provider.create")
            candidate = ProviderCatalogSnapshot(
                self._snapshot.revision + 1,
                self._snapshot.profiles + (profile,),
                self._snapshot.roles,
            )
            return await self._save(candidate, "provider.create")

    async def update_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        validation = _validate_profile(profile)
        if validation is not None:
            return KernelResult.failure(validation)
        async with self._lock:
            conflict = self._conflict(expected_revision, "provider.update")
            if conflict is not None:
                return conflict
            secret_check = await self._check_secret_ref(profile)
            if secret_check is not None:
                return KernelResult.failure(secret_check)
            if all(item.profile_id != profile.profile_id for item in self._snapshot.profiles):
                return _provider_failure(ErrorCode.NOT_FOUND, "Provider profile was not found.", "provider.update")
            profiles = tuple(profile if item.profile_id == profile.profile_id else item for item in self._snapshot.profiles)
            candidate = ProviderCatalogSnapshot(self._snapshot.revision + 1, profiles, self._snapshot.roles)
            return await self._save(candidate, "provider.update")

    async def delete_profile(
        self,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        async with self._lock:
            conflict = self._conflict(expected_revision, "provider.delete")
            if conflict is not None:
                return conflict
            if all(item.profile_id != profile_id for item in self._snapshot.profiles):
                return _provider_failure(ErrorCode.NOT_FOUND, "Provider profile was not found.", "provider.delete")
            if any(mapping.profile_id == profile_id for mapping in self._snapshot.roles):
                return _provider_failure(
                    ErrorCode.CONFLICT,
                    "Provider profile is still assigned to a role.",
                    "provider.delete",
                )
            profiles = tuple(item for item in self._snapshot.profiles if item.profile_id != profile_id)
            candidate = ProviderCatalogSnapshot(self._snapshot.revision + 1, profiles, self._snapshot.roles)
            return await self._save(candidate, "provider.delete")

    async def map_role(
        self,
        role: str,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        clean_role = role.strip()
        if not clean_role:
            return _provider_failure(ErrorCode.INVALID_ARGUMENT, "Provider role is required.", "provider.role.map")
        async with self._lock:
            conflict = self._conflict(expected_revision, "provider.role.map")
            if conflict is not None:
                return conflict
            if all(item.profile_id != profile_id for item in self._snapshot.profiles):
                return _provider_failure(ErrorCode.NOT_FOUND, "Provider profile was not found.", "provider.role.map")
            roles = tuple(item for item in self._snapshot.roles if item.role != clean_role)
            roles += (ProviderRoleMapping(clean_role, profile_id),)
            candidate = ProviderCatalogSnapshot(self._snapshot.revision + 1, self._snapshot.profiles, roles)
            return await self._save(candidate, "provider.role.map")

    async def unmap_role(self, role: str, expected_revision: int) -> KernelResult[ProviderCatalogSnapshot]:
        clean_role = role.strip()
        async with self._lock:
            conflict = self._conflict(expected_revision, "provider.role.unmap")
            if conflict is not None:
                return conflict
            if all(item.role != clean_role for item in self._snapshot.roles):
                return _provider_failure(ErrorCode.NOT_FOUND, "Provider role mapping was not found.", "provider.role.unmap")
            roles = tuple(item for item in self._snapshot.roles if item.role != clean_role)
            candidate = ProviderCatalogSnapshot(self._snapshot.revision + 1, self._snapshot.profiles, roles)
            return await self._save(candidate, "provider.role.unmap")

    async def resolve_profile(
        self,
        profile_id: ProfileId | None = None,
        role: str = "chat",
        *,
        snapshot: ProviderCatalogSnapshot | None = None,
    ) -> KernelResult[ProviderProfile]:
        catalog = snapshot or await self.snapshot()
        selected = profile_id
        if selected is None:
            selected = next((item.profile_id for item in catalog.roles if item.role == role), None)
        if selected is None:
            return _profile_failure(ErrorCode.NOT_FOUND, "No provider profile is assigned to the role.", "provider.resolve")
        profile = next((item for item in catalog.profiles if item.profile_id == selected), None)
        if profile is None:
            return _profile_failure(ErrorCode.NOT_FOUND, "Provider profile was not found.", "provider.resolve")
        return KernelResult.success(profile)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProbeResult]:
        resolved = await self.resolve_profile(profile_id)
        if not resolved.ok:
            assert resolved.error is not None
            return KernelResult.failure(resolved.error)
        assert resolved.value is not None
        profile = resolved.value
        probe = self._probes.get(profile.provider)
        if probe is None:
            return _probe_failure(ErrorCode.NOT_FOUND, "Provider adapter is not available.", "provider.probe")
        result = await probe.probe(profile, self._secrets)
        if not result.ok:
            assert result.error is not None
            return KernelResult.failure(
                KernelError(result.error.code, "Provider probe failed.", result.error.retryable, "provider.probe")
            )
        return KernelResult.success(ProviderProbeResult(profile.profile_id, profile.provider, profile.model, True))

    async def store_secret(self, secret: SecretInput) -> KernelResult[SecretRef]:
        stored = await self._secrets.store(secret)
        if not stored.ok:
            return _secret_failure(
                stored.error.code if stored.error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                "Secret could not be stored.",
                "provider.secret.store",
            )
        assert stored.value is not None
        return KernelResult.success(SecretRef(stored.value.secret_id))

    async def delete_secret(self, reference: SecretRef) -> KernelResult[bool]:
        async with self._lock:
            if any(profile.secret_id == str(reference.secret_id) for profile in self._snapshot.profiles):
                return _bool_failure(
                    ErrorCode.CONFLICT,
                    "Secret is still referenced by a provider profile.",
                    "provider.secret.delete",
                )
            deleted = await self._secrets.delete(reference.secret_id)
            if not deleted.ok:
                return _bool_failure(
                    deleted.error.code if deleted.error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    "Secret could not be deleted.",
                    "provider.secret.delete",
                )
            return deleted

    async def _check_secret_ref(self, profile: ProviderProfile) -> KernelError | None:
        if not profile.secret_id:
            return None
        described = await self._secrets.describe(SecretId(profile.secret_id))
        if not described.ok or described.value is None or not described.value.present:
            return KernelError(ErrorCode.NOT_FOUND, "Provider secret reference was not found.", operation="provider.secret")
        return None

    def _conflict(self, expected_revision: int, operation: str) -> KernelResult[ProviderCatalogSnapshot] | None:
        if self._snapshot.revision == expected_revision:
            return None
        return _provider_failure(ErrorCode.CONFLICT, "Provider catalog revision has changed.", operation)

    async def _save(
        self,
        candidate: ProviderCatalogSnapshot,
        operation: str,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        saved = await self._repository.save(candidate)
        if not saved.ok:
            return _provider_failure(
                saved.error.code if saved.error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                "Provider catalog could not be saved.",
                operation,
            )
        self._snapshot = candidate
        return KernelResult.success(candidate)


def profile_with_secret(profile: ProviderProfile, reference: SecretRef | None) -> ProviderProfile:
    """Attach only an opaque reference; secret material never enters the profile."""

    return replace(profile, secret_id="" if reference is None else str(reference.secret_id))


def _validate_profile(profile: ProviderProfile) -> KernelError | None:
    if not str(profile.profile_id).strip() or not profile.label.strip() or not profile.provider.strip() or not profile.model.strip():
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider profile identity fields are required.", operation="provider.validate")
    parsed = urlparse(profile.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider base URL is invalid.", operation="provider.validate")
    if profile.context_window < 1 or profile.max_output_tokens < 1 or profile.max_output_tokens > profile.context_window:
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider token limits are invalid.", operation="provider.validate")
    if not 0.0 <= profile.temperature <= 2.0:
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider temperature is invalid.", operation="provider.validate")
    return None


def _validate_catalog(snapshot: ProviderCatalogSnapshot) -> KernelError | None:
    if snapshot.revision < 0:
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider catalog revision is invalid.", operation="provider.validate")
    profile_ids = [profile.profile_id for profile in snapshot.profiles]
    if len(set(profile_ids)) != len(profile_ids):
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider profile identifiers must be unique.", operation="provider.validate")
    roles = [mapping.role for mapping in snapshot.roles]
    if len(set(roles)) != len(roles) or any(mapping.profile_id not in profile_ids for mapping in snapshot.roles):
        return KernelError(ErrorCode.CONFIG_INVALID, "Provider role mappings are invalid.", operation="provider.validate")
    return next((error for profile in snapshot.profiles if (error := _validate_profile(profile)) is not None), None)


def _provider_failure(code: ErrorCode, message: str, operation: str) -> KernelResult[ProviderCatalogSnapshot]:
    return KernelResult.failure(KernelError(code, message, operation=operation))


def _profile_failure(code: ErrorCode, message: str, operation: str) -> KernelResult[ProviderProfile]:
    return KernelResult.failure(KernelError(code, message, operation=operation))


def _probe_failure(code: ErrorCode, message: str, operation: str) -> KernelResult[ProviderProbeResult]:
    return KernelResult.failure(KernelError(code, message, operation=operation))


def _secret_failure(code: ErrorCode, message: str, operation: str) -> KernelResult[SecretRef]:
    return KernelResult.failure(KernelError(code, message, operation=operation))


def _bool_failure(code: ErrorCode, message: str, operation: str) -> KernelResult[bool]:
    return KernelResult.failure(KernelError(code, message, operation=operation))
