"""The only frontend-facing Kairo Kernel object."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from kairo_kernel.contracts.content import Message
from kairo_kernel.contracts.enums import ErrorCode, EventType, LifecycleState
from kairo_kernel.contracts.events import EventReplay, LifecycleEvent
from kairo_kernel.contracts.identifiers import KernelId, MemoryId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.interactions import InteractionReceipt, InteractionRequest, InteractionResponse
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus, ShutdownReport, ShutdownRequest
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import (
    ConfigSnapshot,
    MemoryEntry,
    MemoryQuery,
    SecretInput,
    SessionRecord,
    SessionSummary,
)
from kairo_kernel.contracts.tools import ToolDescriptor
from kairo_kernel.contracts.turns import CancelReceipt, TurnAccepted, TurnRequest, TurnResult, TurnSnapshot
from kairo_kernel.engine import EngineOptions, TurnEngine
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import CatalogEntry, McpCatalog, McpHub
from kairo_kernel.ports.tools import ToolRegistryPort
from kairo_kernel.runtime import (
    AsyncLifecycle,
    EventBus,
    EventSubscription,
    InteractionBroker,
    SessionTurnSupervisor,
    WorkspaceLeaseManager,
)
from kairo_kernel.services import (
    ConfigBackup,
    ConfigPatch,
    ConfigurationService,
    ConversationService,
    DiagnosticReport,
    DiagnosticService,
    MemoryService,
    ProviderCatalogSnapshot,
    ProviderProbeResult,
    ProviderService,
    SecretRef,
    SessionService,
    WorkspaceBookmark,
    WorkspacePreview,
    WorkspaceService,
    WorkspaceState,
)
from kairo_kernel.skills import SkillInventory, SkillPackage, SkillRegistry
from kairo_kernel.storage import SQLiteDatabase


@dataclass(frozen=True)
class _KernelParts:
    kernel_id: KernelId
    package_version: str
    shutdown_timeout_seconds: float
    connect_mcp_on_start: bool
    database: SQLiteDatabase
    events: EventBus
    interactions: InteractionBroker
    supervisor: SessionTurnSupervisor
    workspace_leases: WorkspaceLeaseManager
    engine: TurnEngine
    sessions: SessionService
    conversations: ConversationService
    memory: MemoryService
    configuration: ConfigurationService
    workspace: WorkspaceService
    providers: ProviderService
    skills: SkillRegistry
    mcp: McpHub
    diagnostics: DiagnosticService
    engine_options: EngineOptions


class KairoKernel:
    """Stable, UI-neutral façade over all mutable kernel components."""

    def __init__(self, parts: _KernelParts) -> None:
        self._parts = parts
        self._started_at: datetime | None = None
        self._shutdown_report: ShutdownReport | None = None
        self._shutdown_request = ShutdownRequest(parts.shutdown_timeout_seconds)
        self._shutdown_resources: list[str] = []
        self._shutdown_warnings: list[str] = []
        self._active_turn_cancelled = False
        self._lifecycle = AsyncLifecycle(self._start_hook, self._shutdown_hook)
        self._shutdown_lock = asyncio.Lock()
        self.sessions = _Sessions(self, parts.sessions)
        self.conversations = _Conversations(self, parts.conversations)
        self.memory = _Memory(self, parts.memory)
        self.configuration = _Configuration(self, parts.configuration)
        self.workspace = _Workspace(self, parts.workspace)
        self.providers = _Providers(self, parts.providers)
        self.skills = _Skills(self, parts.skills)
        self.mcp = _Mcp(self, parts.mcp)
        self.diagnostics = _Diagnostics(self, parts.diagnostics)
        self.tools = _Tools(self, parts.engine.tools)
        self.events = _Events(self, parts.events)
        self.interactions = _Interactions(self, parts.interactions)

    @property
    def kernel_id(self) -> KernelId:
        return self._parts.kernel_id

    @property
    def state(self) -> LifecycleState:
        return self._lifecycle.state

    async def __aenter__(self) -> KairoKernel:
        result = await self.start()
        if result.error is not None:
            raise RuntimeError(result.error.message)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.shutdown()

    async def start(self) -> KernelResult[LifecycleState]:
        was_created = self.state is LifecycleState.CREATED
        if was_created:
            await self._parts.events.emit(EventType.LIFECYCLE, LifecycleEvent(LifecycleState.STARTING))
        result = await self._lifecycle.start()
        if result.ok and was_created:
            self._started_at = datetime.now(timezone.utc)
            await self._parts.events.emit(EventType.LIFECYCLE, LifecycleEvent(LifecycleState.RUNNING))
        elif self.state is LifecycleState.DEGRADED:
            await self._parts.events.emit(
                EventType.LIFECYCLE,
                LifecycleEvent(LifecycleState.DEGRADED, self._lifecycle.degraded_reason),
            )
        return result

    async def shutdown(self, request: ShutdownRequest | None = None) -> KernelResult[ShutdownReport]:
        shutdown_request = request or ShutdownRequest(self._parts.shutdown_timeout_seconds)
        async with self._shutdown_lock:
            if self._shutdown_report is not None:
                return KernelResult.success(self._shutdown_report)
            self._shutdown_request = shutdown_request
            if self.state is not LifecycleState.STOPPED:
                await self._parts.events.emit(EventType.LIFECYCLE, LifecycleEvent(LifecycleState.STOPPING))
            timeout = max(0.0, shutdown_request.grace_period_seconds) + self._parts.shutdown_timeout_seconds
            result = await self._lifecycle.shutdown(timeout)
            if result.error is not None:
                return KernelResult.failure(result.error)
            report = ShutdownReport(
                LifecycleState.STOPPED,
                self._active_turn_cancelled,
                tuple(self._shutdown_resources),
                tuple(self._shutdown_warnings),
            )
            await self._parts.events.emit(EventType.LIFECYCLE, LifecycleEvent(LifecycleState.STOPPED))
            await self._parts.events.close()
            self._shutdown_report = report
            return KernelResult.success(report)

    async def mark_degraded(self, reason: str) -> None:
        await self._lifecycle.mark_degraded(reason)
        if self.state is not LifecycleState.STOPPED:
            await self._parts.events.emit(EventType.LIFECYCLE, LifecycleEvent(LifecycleState.DEGRADED, reason))

    async def status(self) -> KernelStatus:
        active = await self._parts.supervisor.active()
        session_id = active[0][0] if active else None
        turn_id = active[0][1] if active else None
        workspace = await self._parts.workspace_leases.snapshot()
        return KernelStatus(
            self.kernel_id,
            self.state,
            self._parts.package_version,
            self._started_at,
            workspace.root,
            workspace.revision,
            self._parts.engine_options.profile_id,
            session_id,
            turn_id,
            self._parts.engine_options.authorization_mode,
            self._parts.engine_options.plan_mode,
            self._parts.engine_options.thinking_mode,
            ContextStats(0, 0, 0.0),
            self._lifecycle.degraded_reason,
        )

    async def submit(self, request: TurnRequest) -> KernelResult[TurnAccepted]:
        error = self._mutation_error("turn.submit")
        if error is not None:
            return KernelResult.failure(error)
        return await self._parts.engine.submit(request)

    async def turn(self, turn_id: TurnId) -> KernelResult[TurnSnapshot]:
        error = self._read_error("turn.get")
        if error is not None:
            return KernelResult.failure(error)
        return await self._parts.engine.get(turn_id)

    async def wait(self, turn_id: TurnId, timeout_seconds: float | None = None) -> KernelResult[TurnResult]:
        error = self._read_error("turn.wait")
        if error is not None and error.code is not ErrorCode.KERNEL_CLOSING:
            return KernelResult.failure(error)
        return await self._parts.engine.wait(turn_id, timeout_seconds)

    async def cancel(self, turn_id: TurnId, reason: str = "") -> KernelResult[CancelReceipt]:
        error = self._read_error("turn.cancel")
        if error is not None and error.code is ErrorCode.KERNEL_NOT_RUNNING:
            return KernelResult.failure(error)
        return await self._parts.engine.cancel(turn_id, reason)

    def _read_error(self, operation: str) -> KernelError | None:
        if self.state in {LifecycleState.CREATED, LifecycleState.STARTING}:
            return KernelError(ErrorCode.KERNEL_NOT_RUNNING, "Kernel is not running.", operation=operation)
        if self.state in {LifecycleState.STOPPING, LifecycleState.STOPPED}:
            return KernelError(ErrorCode.KERNEL_CLOSING, "Kernel is closing or stopped.", operation=operation)
        return None

    def _mutation_error(self, operation: str) -> KernelError | None:
        error = self._read_error(operation)
        if error is not None:
            return error
        if self.state is LifecycleState.DEGRADED:
            return KernelError(ErrorCode.KERNEL_DEGRADED, "Kernel is degraded; mutations are disabled.", operation=operation)
        return None

    async def _start_hook(self) -> None:
        await self._parts.database.open()
        if self._parts.connect_mcp_on_start:
            await self._parts.mcp.connect_all()

    async def _shutdown_hook(self) -> None:
        active = await self._parts.supervisor.close_admission()
        request = self._shutdown_request
        if request.cancel_active_turn:
            for _, turn_id in active:
                cancelled = await self._parts.engine.cancel(turn_id, "Kernel shutdown requested.")
                self._active_turn_cancelled = self._active_turn_cancelled or (
                    cancelled.value is not None and cancelled.value.requested
                )
        idle = await self._parts.supervisor.wait_idle(request.grace_period_seconds)
        if not idle:
            self._shutdown_warnings.append("Active turns did not finish within the grace period.")
        await self._parts.interactions.shutdown()
        self._shutdown_resources.append("interactions")
        await self._parts.mcp.close()
        self._shutdown_resources.append("mcp")
        await self._parts.workspace_leases.close()
        self._shutdown_resources.append("workspace")
        await self._parts.database.close()
        self._shutdown_resources.append("database")


class _Sessions:
    def __init__(self, kernel: KairoKernel, service: SessionService) -> None:
        self._kernel, self._service = kernel, service

    @property
    def revision(self) -> int:
        return self._service.revision

    async def list(self) -> KernelResult[tuple[SessionSummary, ...]]:
        return await self._service.list()

    async def get(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        return await self._service.get(session_id)

    async def create(
        self,
        name: str,
        messages: tuple[Message, ...] = (),
        *,
        session_id: SessionId | None = None,
    ) -> KernelResult[SessionRecord]:
        error = self._kernel._mutation_error("session.create")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.create(name, messages, session_id=session_id)
        )

    async def rename(self, session_id: SessionId, name: str) -> KernelResult[SessionRecord]:
        error = self._kernel._mutation_error("session.rename")
        return KernelResult.failure(error) if error is not None else await self._service.rename(session_id, name)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        error = self._kernel._mutation_error("session.delete")
        return KernelResult.failure(error) if error is not None else await self._service.delete(session_id)

    async def search(self, text: str, *, limit: int = 50) -> KernelResult[tuple[SessionSummary, ...]]:
        return await self._service.search(text, limit=limit)

    async def export(self, session_id: SessionId, *, format: str = "json") -> KernelResult[str]:
        return await self._service.export(session_id, format=format)


class _Conversations:
    def __init__(self, kernel: KairoKernel, service: ConversationService) -> None:
        self._kernel, self._service = kernel, service

    async def history(self, session_id: SessionId) -> KernelResult[tuple[Message, ...]]:
        return await self._service.history(session_id)

    async def clear(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        return await self._record_mutation("conversation.clear", lambda: self._service.clear(session_id))

    async def undo(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        return await self._record_mutation("conversation.undo", lambda: self._service.undo(session_id))

    async def compress(
        self,
        session_id: SessionId,
        summary: str,
        *,
        preserve_recent_turns: int = 4,
    ) -> KernelResult[SessionRecord]:
        return await self._record_mutation(
            "conversation.compress",
            lambda: self._service.compress(
                session_id,
                summary,
                preserve_recent_turns=preserve_recent_turns,
            ),
        )

    async def _record_mutation(
        self,
        operation: str,
        call: Callable[[], Awaitable[KernelResult[SessionRecord]]],
    ) -> KernelResult[SessionRecord]:
        error = self._kernel._mutation_error(operation)
        if error is not None:
            return KernelResult.failure(error)
        return await call()


class _Memory:
    def __init__(self, kernel: KairoKernel, service: MemoryService) -> None:
        self._kernel, self._service = kernel, service

    async def search(self, query: MemoryQuery) -> KernelResult[tuple[MemoryEntry, ...]]:
        return await self._service.search(query)

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        return await self._service.get(memory_id)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        error = self._kernel._mutation_error("memory.put")
        return KernelResult.failure(error) if error is not None else await self._service.put(entry)

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        error = self._kernel._mutation_error("memory.delete")
        return KernelResult.failure(error) if error is not None else await self._service.delete(memory_id)


class _Configuration:
    def __init__(self, kernel: KairoKernel, service: ConfigurationService) -> None:
        self._kernel, self._service = kernel, service

    async def snapshot(self) -> ConfigSnapshot:
        return await self._service.snapshot()

    async def export_json(self) -> str:
        return await self._service.export_json()

    async def validate(self, values: JsonObject) -> KernelResult[ConfigSnapshot]:
        return await self._service.validate(values)

    async def patch(self, patch: ConfigPatch) -> KernelResult[ConfigSnapshot]:
        error = self._kernel._mutation_error("config.patch")
        return KernelResult.failure(error) if error is not None else await self._service.patch(patch)

    async def backup(self) -> KernelResult[ConfigBackup]:
        error = self._kernel._mutation_error("config.backup")
        return KernelResult.failure(error) if error is not None else await self._service.backup()

    async def import_json(self, payload: str, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        error = self._kernel._mutation_error("config.import")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.import_json(payload, expected_revision)
        )

    async def restore(self, backup: ConfigBackup, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        error = self._kernel._mutation_error("config.restore")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.restore(backup, expected_revision)
        )


class _Workspace:
    def __init__(self, kernel: KairoKernel, service: WorkspaceService) -> None:
        self._kernel, self._service = kernel, service

    async def snapshot(self) -> WorkspaceState:
        return await self._service.snapshot()

    async def preview(self, relative_path: str = ".") -> KernelResult[WorkspacePreview]:
        return await self._service.preview(relative_path)

    async def move(self, target: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.move")
        return KernelResult.failure(error) if error is not None else await self._service.move(target, expected_revision)

    async def save_bookmark(self, bookmark: WorkspaceBookmark, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.bookmark.save")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.save_bookmark(bookmark.name, bookmark.path, expected_revision)
        )

    async def remove_bookmark(self, name: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.bookmark.remove")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.remove_bookmark(name, expected_revision)
        )


class _Providers:
    def __init__(self, kernel: KairoKernel, service: ProviderService) -> None:
        self._kernel, self._service = kernel, service

    async def snapshot(self) -> ProviderCatalogSnapshot:
        return await self._service.snapshot()

    async def resolve(self, profile_id: ProfileId | None = None, role: str = "") -> KernelResult[ProviderProfile]:
        return await self._service.resolve_profile(profile_id, role)

    async def probe(self, profile_id: ProfileId) -> KernelResult[ProviderProbeResult]:
        return await self._service.probe(profile_id)

    async def store_secret(self, secret: SecretInput) -> KernelResult[SecretRef]:
        error = self._kernel._mutation_error("provider.secret.store")
        return KernelResult.failure(error) if error is not None else await self._service.store_secret(secret)

    async def create_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.create")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.create_profile(profile, expected_revision)
        )

    async def update_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.update")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.update_profile(profile, expected_revision)
        )

    async def delete_profile(
        self,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.delete")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.delete_profile(profile_id, expected_revision)
        )

    async def map_role(
        self,
        role: str,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.role.map")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.map_role(role, profile_id, expected_revision)
        )

    async def unmap_role(self, role: str, expected_revision: int) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.role.unmap")
        return (
            KernelResult.failure(error)
            if error is not None
            else await self._service.unmap_role(role, expected_revision)
        )

    async def delete_secret(self, reference: SecretRef) -> KernelResult[bool]:
        error = self._kernel._mutation_error("provider.secret.delete")
        return KernelResult.failure(error) if error is not None else await self._service.delete_secret(reference)


class _Skills:
    def __init__(self, kernel: KairoKernel, registry: SkillRegistry) -> None:
        self._kernel, self._registry = kernel, registry

    async def inspect(self) -> SkillInventory:
        return await self._registry.inspect()

    async def active(self) -> tuple[SkillPackage, ...]:
        return await self._registry.active()

    async def reload(self) -> KernelResult[SkillInventory]:
        error = self._kernel._mutation_error("skills.reload")
        if error is not None:
            return KernelResult.failure(error)
        return KernelResult.success(await self._registry.reload())

    async def trust(self, expected_digest: str) -> KernelResult[SkillInventory]:
        error = self._kernel._mutation_error("skills.trust")
        return KernelResult.failure(error) if error is not None else KernelResult.success(await self._registry.trust(expected_digest))

    async def revoke(self) -> KernelResult[bool]:
        error = self._kernel._mutation_error("skills.revoke")
        return KernelResult.failure(error) if error is not None else KernelResult.success(await self._registry.revoke())


class _Mcp:
    def __init__(self, kernel: KairoKernel, hub: McpHub) -> None:
        self._kernel, self._hub = kernel, hub

    def catalog(self) -> tuple[CatalogEntry, ...]:
        return self._hub.catalog()

    async def connect(self) -> KernelResult[tuple[McpCatalog, ...]]:
        error = self._kernel._mutation_error("mcp.connect")
        if error is not None:
            return KernelResult.failure(error)
        return KernelResult.success(await self._hub.connect_all())

    async def refresh(self) -> KernelResult[tuple[McpCatalog, ...]]:
        error = self._kernel._mutation_error("mcp.refresh")
        if error is not None:
            return KernelResult.failure(error)
        return KernelResult.success(await self._hub.refresh_all())


class _Diagnostics:
    def __init__(self, kernel: KairoKernel, service: DiagnosticService) -> None:
        self._kernel, self._service = kernel, service

    async def local(self) -> KernelResult[DiagnosticReport]:
        error = self._kernel._read_error("diagnostics.local")
        return KernelResult.failure(error) if error is not None else KernelResult.success(await self._service.local())

    async def full(self) -> KernelResult[DiagnosticReport]:
        error = self._kernel._read_error("diagnostics.full")
        return KernelResult.failure(error) if error is not None else KernelResult.success(await self._service.full())


class _Tools:
    def __init__(self, kernel: KairoKernel, registry: ToolRegistryPort) -> None:
        self._kernel, self._registry = kernel, registry

    async def list(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        error = self._kernel._read_error("tools.list")
        return KernelResult.failure(error) if error is not None else KernelResult.success(await self._registry.list())

    async def reload(self) -> KernelResult[tuple[ToolDescriptor, ...]]:
        error = self._kernel._mutation_error("tools.reload")
        return KernelResult.failure(error) if error is not None else await self._registry.reload()


class _Events:
    def __init__(self, kernel: KairoKernel, bus: EventBus) -> None:
        self._kernel, self._bus = kernel, bus

    async def snapshot(self, after_sequence: int = 0, limit: int = 1000) -> EventReplay:
        return await self._bus.snapshot(after_sequence, limit)

    async def subscribe(self, after_sequence: int = 0, queue_size: int | None = None) -> EventSubscription:
        return await self._bus.subscribe(after_sequence, queue_size)


class _Interactions:
    def __init__(self, kernel: KairoKernel, broker: InteractionBroker) -> None:
        self._kernel, self._broker = kernel, broker

    async def pending(self) -> tuple[InteractionRequest, ...]:
        return await self._broker.pending()

    async def respond(self, response: InteractionResponse) -> KernelResult[InteractionReceipt]:
        error = self._kernel._mutation_error("interaction.respond")
        return KernelResult.failure(error) if error is not None else await self._broker.respond(response)
