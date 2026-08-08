"""Shared profile, retry, error and cancellation behavior."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from kairo_kernel.contracts.enums import ErrorCode, ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderFailure, ProviderProfile, ProviderStreamEvent
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.providers.http import AsyncHttpTransport, HttpRequest, HttpStream, UrllibAsyncHttpTransport


class SecretResolver(Protocol):
    async def resolve(self, secret_id: str) -> str: ...


class EmptySecretResolver:
    async def resolve(self, secret_id: str) -> str:
        return ""


@dataclass(frozen=True)
class AdapterOptions:
    max_retries: int = 2
    retry_base_delay: float = 0.25
    timeout_seconds: float = 60.0


class ProviderAdapterBase:
    provider_name = ""

    def __init__(
        self,
        profiles: tuple[ProviderProfile, ...],
        *,
        transport: AsyncHttpTransport | None = None,
        secrets: SecretResolver | None = None,
        role_profiles: Mapping[str, ProfileId] | None = None,
        default_profile: ProfileId | None = None,
        options: AdapterOptions = AdapterOptions(),
    ):
        self._profiles = {profile.profile_id: profile for profile in profiles}
        self._transport = transport or UrllibAsyncHttpTransport()
        self._secrets = secrets or EmptySecretResolver()
        self._role_profiles = dict(role_profiles or {})
        self._default_profile = default_profile or (profiles[0].profile_id if profiles else None)
        self._options = options

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        selected = profile_id or self._role_profiles.get(role) or self._default_profile
        if selected is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "No provider profile is configured."))
        profile = self._profiles.get(selected)
        if profile is None or profile.provider != self.provider_name:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Provider profile '{selected}' was not found.")
            )
        return KernelResult.success(profile)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        resolved = await self.resolve_profile(profile_id, "chat")
        if not resolved.ok:
            return resolved
        profile = resolved.value
        assert profile is not None
        request = await self._probe_request(profile)
        stream, failure = await self._open_with_retries(request, _NeverCancelled())
        if failure is not None:
            return KernelResult.failure(_kernel_error(failure, "probe provider"))
        assert stream is not None
        await stream.close()
        return KernelResult.success(profile)

    async def _probe_request(self, profile: ProviderProfile) -> HttpRequest:
        raise NotImplementedError

    async def _headers(self, profile: ProviderProfile, extra: tuple[tuple[str, str], ...] = ()) -> tuple[tuple[str, str], ...]:
        secret = await self._secrets.resolve(profile.secret_id) if profile.secret_id else ""
        return self._auth_headers(secret) + (("content-type", "application/json"),) + extra

    def _auth_headers(self, secret: str) -> tuple[tuple[str, str], ...]:
        return (("authorization", f"Bearer {secret}"),) if secret else ()

    async def _open_with_retries(
        self,
        request: HttpRequest,
        cancellation: CancellationToken,
    ) -> tuple[HttpStream | None, ProviderFailure | None]:
        for attempt in range(self._options.max_retries + 1):
            if cancellation.cancelled:
                return None, _cancelled_failure()
            try:
                stream = await self._transport.open(request)
            except Exception as error:
                if attempt < self._options.max_retries and not await self._backoff(attempt, cancellation):
                    continue
                if cancellation.cancelled:
                    return None, _cancelled_failure()
                return None, ProviderFailure(ProviderFailureKind.CONNECTION, str(error), True)
            if 200 <= stream.status_code < 300:
                return stream, None
            body = await stream.read()
            await stream.close()
            failure = failure_from_http(stream.status_code, body)
            if failure.retryable and attempt < self._options.max_retries:
                if await self._backoff(attempt, cancellation):
                    return None, _cancelled_failure()
                continue
            return None, failure
        return None, ProviderFailure(ProviderFailureKind.CONNECTION, "Provider request failed.", True)

    async def _backoff(self, attempt: int, cancellation: CancellationToken) -> bool:
        delay = self._options.retry_base_delay * (2**attempt)
        if delay <= 0:
            await asyncio.sleep(0)
            return cancellation.cancelled
        sleeper = asyncio.create_task(asyncio.sleep(delay))
        cancelled = asyncio.create_task(cancellation.wait())
        done, pending = await asyncio.wait((sleeper, cancelled), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return cancellation.cancelled or cancelled in done

    async def _failure_event(self, failure: ProviderFailure) -> AsyncIterator[ProviderStreamEvent]:
        yield ProviderStreamEvent(ProviderStreamKind.FAILED, failure=failure)


class _NeverCancelled:
    @property
    def cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await asyncio.Future()


def _cancelled_failure() -> ProviderFailure:
    return ProviderFailure(ProviderFailureKind.CANCELLED, "Provider request cancelled.", False)


def cancelled_event() -> ProviderStreamEvent:
    return ProviderStreamEvent(ProviderStreamKind.FAILED, failure=_cancelled_failure())


def failure_from_http(status_code: int, body: bytes) -> ProviderFailure:
    message = _error_message(body) or f"HTTP {status_code}"
    lowered = message.lower()
    context_markers = (
        "context length",
        "context window",
        "maximum context",
        "too many tokens",
        "context_length_exceeded",
        "token limit",
    )
    if status_code in (400, 413) and any(marker in lowered for marker in context_markers):
        return ProviderFailure(ProviderFailureKind.CONTEXT, message, False, status_code)
    if status_code in (401, 403):
        return ProviderFailure(ProviderFailureKind.AUTH, message, False, status_code)
    if status_code == 429:
        return ProviderFailure(ProviderFailureKind.RATE_LIMIT, message, True, status_code)
    if status_code >= 500:
        return ProviderFailure(ProviderFailureKind.SERVER, message, True, status_code)
    return ProviderFailure(ProviderFailureKind.CLIENT, message, False, status_code)


def provider_error(message: str, *, retryable: bool = False) -> ProviderStreamEvent:
    return ProviderStreamEvent(
        ProviderStreamKind.FAILED,
        failure=ProviderFailure(ProviderFailureKind.CLIENT, message, retryable),
    )


def _error_message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            nested_message = error.get("message")
            if isinstance(nested_message, str):
                return nested_message
        if isinstance(error, str):
            return error
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return text


def _kernel_error(failure: ProviderFailure, operation: str) -> KernelError:
    codes = {
        ProviderFailureKind.AUTH: ErrorCode.PROVIDER_AUTH,
        ProviderFailureKind.RATE_LIMIT: ErrorCode.PROVIDER_RATE_LIMIT,
        ProviderFailureKind.SERVER: ErrorCode.PROVIDER_SERVER,
        ProviderFailureKind.CONNECTION: ErrorCode.PROVIDER_CONNECTION,
        ProviderFailureKind.CONTEXT: ErrorCode.PROVIDER_CONTEXT,
        ProviderFailureKind.CLIENT: ErrorCode.PROVIDER_CLIENT,
        ProviderFailureKind.CANCELLED: ErrorCode.PROVIDER_CLIENT,
    }
    return KernelError(codes[failure.kind], failure.message, failure.retryable, operation)
