"""Dynamic provider routing over a live catalog snapshot."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from kairo_kernel.contracts.enums import ErrorCode, ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import (
    ProviderFailure,
    ProviderProfile,
    ProviderRequest,
    ProviderStreamEvent,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.ports.services import SecretPort
from kairo_kernel.providers.anthropic import AnthropicMessagesAdapter
from kairo_kernel.providers.base import SecretResolver
from kairo_kernel.providers.http import AsyncHttpTransport
from kairo_kernel.providers.openai_chat import OpenAIChatCompletionsAdapter
from kairo_kernel.providers.openai_responses import OpenAIResponsesAdapter
from kairo_kernel.services.providers import ProviderCatalogSnapshot, ProviderRoleMapping

RoutedAdapter = OpenAIResponsesAdapter | OpenAIChatCompletionsAdapter | AnthropicMessagesAdapter
CatalogSource = Callable[[], Awaitable[ProviderCatalogSnapshot]]


class ProviderRouter:
    """Route profiles and streams to per-kind adapters rebuilt on catalog change."""

    def __init__(
        self,
        catalog: CatalogSource,
        secrets: SecretResolver,
        *,
        transports: Mapping[str, AsyncHttpTransport] | None = None,
    ) -> None:
        self._catalog = catalog
        self._secrets = secrets
        self._transports = dict(transports or {})
        self._adapters: dict[str, tuple[str, RoutedAdapter]] = {}

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        snapshot = await self._catalog()
        selected = profile_id or next((mapping.profile_id for mapping in snapshot.roles if mapping.role == role), None)
        if selected is None and snapshot.profiles:
            selected = snapshot.profiles[0].profile_id
        if selected is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "No provider profile is configured."))
        profile = next((item for item in snapshot.profiles if item.profile_id == selected), None)
        if profile is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Provider profile '{selected}' was not found.")
            )
        return KernelResult.success(profile)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        return self._stream(request, cancellation)

    async def _stream(
        self, request: ProviderRequest, cancellation: CancellationToken
    ) -> AsyncIterator[ProviderStreamEvent]:
        snapshot = await self._catalog()
        adapter = await self._adapter(snapshot, request.profile.provider)
        if adapter is None:
            yield ProviderStreamEvent(
                ProviderStreamKind.FAILED,
                failure=ProviderFailure(
                    ProviderFailureKind.CLIENT,
                    f"Unsupported provider kind: {request.profile.provider}",
                    False,
                ),
            )
            return
        async for event in adapter.stream(request, cancellation):
            yield event

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        resolved = await self.resolve_profile(profile_id, "chat")
        if resolved.error is not None or resolved.value is None:
            return KernelResult.failure(
                resolved.error or KernelError(ErrorCode.NOT_FOUND, "Provider profile was not found.")
            )
        snapshot = await self._catalog()
        adapter = await self._adapter(snapshot, resolved.value.provider)
        if adapter is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Unsupported provider kind: {resolved.value.provider}")
            )
        return await adapter.probe(profile_id)

    async def _adapter(self, snapshot: ProviderCatalogSnapshot, kind: str) -> RoutedAdapter | None:
        profiles = tuple(profile for profile in snapshot.profiles if profile.provider == kind)
        digest = _digest(kind, profiles, snapshot.roles)
        cached = self._adapters.get(kind)
        if cached is not None and cached[0] == digest:
            return cached[1]
        adapter = self._build(kind, profiles, snapshot)
        if adapter is None:
            return None
        self._adapters[kind] = (digest, adapter)
        return adapter

    def _build(self, kind: str, profiles: tuple[ProviderProfile, ...], snapshot: ProviderCatalogSnapshot) -> RoutedAdapter | None:
        roles = {
            mapping.role: mapping.profile_id
            for mapping in snapshot.roles
            if any(profile.profile_id == mapping.profile_id for profile in profiles)
        }
        default = next((profile.profile_id for profile in profiles), None)
        transport = self._transports.get(kind)
        options: dict[str, object] = {"secrets": self._secrets, "role_profiles": roles, "default_profile": default}
        if transport is not None:
            options["transport"] = transport
        if kind == "openai_responses":
            return OpenAIResponsesAdapter(profiles, **options)  # type: ignore[arg-type]
        if kind == "openai_chat":
            return OpenAIChatCompletionsAdapter(profiles, **options)  # type: ignore[arg-type]
        if kind == "anthropic":
            return AnthropicMessagesAdapter(profiles, **options)  # type: ignore[arg-type]
        return None


class RouterProbe:
    """Adapt the router to the ProviderService probe port."""

    def __init__(self, router: ProviderRouter) -> None:
        self._router = router

    async def probe(self, profile: ProviderProfile, secrets: SecretPort) -> KernelResult[ProviderProfile]:
        del secrets
        return await self._router.probe(profile.profile_id)


def _digest(kind: str, profiles: tuple[ProviderProfile, ...], roles: tuple[ProviderRoleMapping, ...]) -> str:
    parts = [kind]
    parts.extend(sorted(profile.to_json() for profile in profiles))
    parts.extend(sorted(f"{mapping.role}={mapping.profile_id}" for mapping in roles))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
