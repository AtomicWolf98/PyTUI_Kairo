from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from kairo_kernel import KairoKernel, KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole, ProviderStreamKind
from kairo_kernel.contracts.identifiers import MessageId, ProfileId, SecretId, SessionId
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest
from kairo_kernel.contracts.support import SecretDescriptor, SecretInput
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.providers.http import HttpRequest
from kairo_kernel.providers.router import ProviderRouter, RouterProbe
from kairo_kernel.services.config_document import DocumentProviderCatalog, KernelConfigDocument, KernelConfigStore
from kairo_kernel.services.providers import (
    InMemoryProviderCatalog,
    ProviderCatalogSnapshot,
    ProviderRoleMapping,
    ProviderService,
)


def _profile(identifier: str, kind: str) -> ProviderProfile:
    return ProviderProfile(
        ProfileId(identifier), identifier, kind, "model", f"https://{kind}.example.test/v1", 32000, 2000, 0.2
    )


class Secret:
    async def resolve(self, secret_id: str) -> str:
        return "secret"


class Cancellation:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await self._event.wait()


class MockStream:
    def __init__(self, body: bytes = b"{}", status_code: int = 200):
        self._body = body
        self._status_code = status_code

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        return ()

    def iter_bytes(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        yield self._body

    async def read(self) -> bytes:
        return self._body

    async def close(self) -> None:
        return None


class FakeTransport:
    def __init__(self, body: bytes = b"{}", status_code: int = 200):
        self._stream = MockStream(body, status_code)
        self.requests: list[HttpRequest] = []

    async def open(self, request: HttpRequest) -> MockStream:
        self.requests.append(request)
        return self._stream


class CatalogCell:
    def __init__(self, snapshot: ProviderCatalogSnapshot) -> None:
        self._snapshot = snapshot

    def set(self, snapshot: ProviderCatalogSnapshot) -> None:
        self._snapshot = snapshot

    async def snapshot(self) -> ProviderCatalogSnapshot:
        return self._snapshot


def _message() -> Message:
    return Message(MessageId("m-1"), MessageRole.USER, MessageKind.CHAT, (TextBlock("hi"),))


def test_stream_routes_per_profile_kind() -> None:
    async def exercise() -> None:
        chat = _profile("openai/gpt", "openai_chat")
        claude = _profile("anthropic/claude", "anthropic")
        cell = CatalogCell(ProviderCatalogSnapshot(0, (chat, claude), ()))
        transports = {
            "openai_chat": FakeTransport(status_code=400),
            "anthropic": FakeTransport(status_code=400),
        }
        router = ProviderRouter(cell.snapshot, Secret(), transports=transports)

        events = [event async for event in router.stream(ProviderRequest(chat, (_message(),)), Cancellation())]
        assert events and events[-1].kind is ProviderStreamKind.FAILED
        assert len(transports["openai_chat"].requests) == 1
        assert transports["anthropic"].requests == []

        events = [event async for event in router.stream(ProviderRequest(claude, (_message(),)), Cancellation())]
        assert events and events[-1].kind is ProviderStreamKind.FAILED
        assert len(transports["anthropic"].requests) == 1

    asyncio.run(exercise())


def test_resolve_follows_role_then_first_profile_and_updates_live() -> None:
    async def exercise() -> None:
        chat = _profile("openai/gpt", "openai_chat")
        claude = _profile("anthropic/claude", "anthropic")
        cell = CatalogCell(
            ProviderCatalogSnapshot(0, (chat, claude), (ProviderRoleMapping("chat", chat.profile_id),))
        )
        router = ProviderRouter(cell.snapshot, Secret())

        resolved = await router.resolve_profile(None, "chat")
        assert resolved.ok and resolved.value == chat
        fallback = await router.resolve_profile(None, "plan")
        assert fallback.ok and fallback.value == chat  # first profile fallback
        missing = await router.resolve_profile(ProfileId("nope"), "chat")
        assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND

        cell.set(
            ProviderCatalogSnapshot(
                1, (chat, claude), (ProviderRoleMapping("chat", claude.profile_id),)
            )
        )
        rerouted = await router.resolve_profile(None, "chat")
        assert rerouted.ok and rerouted.value == claude

    asyncio.run(exercise())


def test_probe_delegates_to_kind_adapter() -> None:
    async def exercise() -> None:
        chat = _profile("openai/gpt", "openai_chat")
        cell = CatalogCell(ProviderCatalogSnapshot(0, (chat,), ()))
        transport = FakeTransport(status_code=200)
        router = ProviderRouter(cell.snapshot, Secret(), transports={"openai_chat": transport})

        probed = await router.probe(chat.profile_id)
        assert probed.ok and probed.value == chat
        assert transport.requests and transport.requests[0].url.startswith(chat.base_url)

    asyncio.run(exercise())


def test_router_probe_plugs_into_provider_service() -> None:
    class FakeSecrets:
        async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
            return KernelResult.success(SecretDescriptor(secret_id, "test", "", False))

        async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))

        async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
            return KernelResult.success(SecretDescriptor(secret.secret_id, "test", "***", True))

        async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
            return KernelResult.success(True)

    async def exercise() -> None:
        chat = _profile("openai/gpt", "openai_chat")
        service = ProviderService(
            InMemoryProviderCatalog(), FakeSecrets(), (), ProviderCatalogSnapshot(0, (chat,), ())
        )
        router = ProviderRouter(service.snapshot, Secret(), transports={"openai_chat": FakeTransport(status_code=200)})
        service.register_probe("openai_chat", RouterProbe(router))

        probed = await service.probe(chat.profile_id)
        assert probed.ok and probed.value is not None and probed.value.reachable

    asyncio.run(exercise())


def test_factory_accepts_mixed_provider_kinds(tmp_path: Path) -> None:
    root = str(tmp_path)
    config = KernelConfig(
        root,
        database_path=root + "/kernel.db",
        profiles=(_profile("openai/gpt", "openai_chat"), _profile("anthropic/claude", "anthropic")),
        provider_roles=(ProviderRoleMapping("chat", ProfileId("openai/gpt")),),
        enable_builtin_tools=False,
    )
    kernel = build_kernel(config)  # previously raised ValueError("cannot mix provider kinds")
    assert isinstance(kernel, KairoKernel)
    assert type(kernel._parts.engine.provider).__name__ == "ProviderRouter"


def test_factory_persists_catalog_via_document_override(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path
        store = KernelConfigStore(root / "config-v1.json")
        seeded = await store.save(KernelConfigDocument(profiles=(_profile("openai/gpt", "openai_chat"),)))
        assert seeded.ok
        config = KernelConfig(
            str(root),
            database_path=str(root / "kernel.db"),
            default_session_id=SessionId("session-1"),
            enable_builtin_tools=False,
        )
        kernel = build_kernel(config, KernelDependencies(provider_catalog=DocumentProviderCatalog(store)))
        async with kernel:
            created = await kernel.providers.create_profile(_profile("anthropic/claude", "anthropic"), 0)
            assert created.ok
        document = (await store.load()).value
        assert document is not None
        assert {profile.provider for profile in document.profiles} == {"openai_chat", "anthropic"}

    asyncio.run(exercise())
