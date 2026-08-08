from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import (
    ErrorCode,
    MessageKind,
    MessageRole,
    ProviderFailureKind,
    ProviderStreamKind,
    TurnStatus,
)
from kairo_kernel.contracts.identifiers import MessageId, ProfileId, ResourceId, SessionId
from kairo_kernel.contracts.providers import ProviderFailure, ProviderProfile, ProviderRequest, ProviderStreamEvent
from kairo_kernel.contracts.support import SessionRecord
from kairo_kernel.contracts.turns import TurnRequest
from kairo_kernel.errors import KernelResult
from kairo_kernel.mcp import McpClient, McpServerConfig, McpServerTrustStore
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.storage import BlobStore
from kairo_kernel.testing import EmptyTools, InMemorySessions


class FaultProvider:
    profile = ProviderProfile(ProfileId("fault/model"), "Fault", "fault", "model", "https://invalid", 1000, 100, 0)

    async def resolve_profile(self, profile_id: ProfileId | None, role: str) -> KernelResult[ProviderProfile]:
        del profile_id, role
        return KernelResult.success(self.profile)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProfile]:
        del profile_id
        return KernelResult.success(self.profile)

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        del request, cancellation
        return self._stream()

    async def _stream(self) -> AsyncIterator[ProviderStreamEvent]:
        yield ProviderStreamEvent(
            ProviderStreamKind.FAILED,
            failure=ProviderFailure(ProviderFailureKind.CONNECTION, "injected provider fault", True),
        )


class FailingTransport:
    def __init__(self) -> None:
        self.closed = False

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        del message
        raise ConnectionError("injected MCP disconnect")

    async def notify(self, message: dict[str, object]) -> None:
        del message

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_blob_write_fault_is_typed_and_leaves_no_partial_file(tmp_path: Path) -> None:
    obstacle = tmp_path / "not-a-directory"
    obstacle.write_text("blocked", encoding="utf-8")
    store = BlobStore(obstacle)
    result = await store.put(b"payload")
    assert result.error is not None
    assert result.error.code is ErrorCode.INTERNAL
    assert tuple(tmp_path.glob("*.tmp")) == ()
    assert not await store.exists(ResourceId("0" * 64))


@pytest.mark.asyncio
async def test_mcp_transport_fault_closes_without_catalog_pollution(tmp_path: Path) -> None:
    config = McpServerConfig("fault", "http", url="https://invalid.example")
    trust = McpServerTrustStore(tmp_path / "trust.json")
    trust.trust(config, config.digest)
    transport = FailingTransport()
    client = McpClient(config, trust, lambda _: transport)
    with pytest.raises(ConnectionError, match="injected MCP disconnect"):
        await client.connect()
    await client.close()
    assert transport.closed
    assert client.catalog.all_entries() == ()


@pytest.mark.asyncio
async def test_provider_fault_becomes_one_failed_terminal_turn(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    session = InMemorySessions(
        (
            SessionRecord(
                SessionId("session"),
                "Session",
                (Message(MessageId("system"), MessageRole.SYSTEM, MessageKind.CHAT, (TextBlock("system"),)),),
                now,
                now,
            ),
        )
    )
    kernel = build_kernel(
        KernelConfig(
            str(tmp_path),
            database_path=str(tmp_path / "kernel.db"),
            default_session_id=SessionId("session"),
            enable_builtin_tools=False,
        ),
        KernelDependencies(provider=FaultProvider(), tools=EmptyTools(), sessions=session),
    )
    async with kernel:
        accepted = await kernel.submit(TurnRequest("fail", SessionId("session")))
        assert accepted.value is not None
        result = await kernel.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.FAILED
        replay = await kernel.events.snapshot()
        terminals = [
            event
            for event in replay.events
            if event.turn_id == accepted.value.turn_id
            and getattr(event.payload, "status", None) in {TurnStatus.SUCCEEDED, TurnStatus.CANCELLED, TurnStatus.FAILED}
        ]
        assert len(terminals) == 1
