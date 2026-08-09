"""The only frontend-facing Kairo Kernel object."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kairo_kernel.contracts.commands import CommandOutcome, KernelCommand, ParsedCommand
from kairo_kernel.contracts.content import Message
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    EventType,
    InteractionAction,
    InteractionKind,
    LifecycleState,
    OperationScope,
    TurnStatus,
)
from kairo_kernel.contracts.events import ChangeEvent, EventReplay, InteractionEvent, LifecycleEvent
from kairo_kernel.contracts.identifiers import InteractionId, KernelId, MemoryId, ProfileId, SessionId, TurnId
from kairo_kernel.contracts.interactions import (
    InteractionChoice,
    InteractionReceipt,
    InteractionRequest,
    InteractionResponse,
)
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.lifecycle import (
    ContextStats,
    KernelCapabilities,
    KernelStatus,
    ShutdownReport,
    ShutdownRequest,
)
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
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
from kairo_kernel.contracts.turns import (
    ActiveTurn,
    CancelReceipt,
    TurnAccepted,
    TurnRequest,
    TurnResult,
    TurnSnapshot,
)
from kairo_kernel.engine import EngineOptions, TurnEngine, estimate_context_tokens
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import CatalogEntry, McpCatalog, McpClient, McpError, McpHub, McpProtocolError, McpTrustError
from kairo_kernel.ports.tools import ToolRegistryPort
from kairo_kernel.runtime import (
    AsyncLifecycle,
    CancellationSource,
    EventBus,
    EventSubscription,
    InteractionBroker,
    SessionTurnSupervisor,
    WorkspaceLeaseManager,
)
from kairo_kernel.services import (
    CapabilityService,
    ChangedFiles,
    CommandService,
    CommandServices,
    ConfigBackup,
    ConfigPatch,
    ConfigurationService,
    ConversationService,
    DiagnosticReport,
    DiagnosticService,
    MemoryService,
    PreferencesService,
    ProviderCatalogSnapshot,
    ProviderProbeResult,
    ProviderService,
    SecretRef,
    SessionService,
    WorkspaceBookmark,
    WorkspaceDiff,
    WorkspacePreview,
    WorkspaceService,
    WorkspaceState,
    WorkspaceTree,
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
    capabilities: CapabilityService
    preferences: PreferencesService
    mcp_call_timeout_seconds: float = 30.0
    restore_provider_catalog: bool = False


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
        self.mcp = _Mcp(self, parts.mcp, timeout_seconds=parts.mcp_call_timeout_seconds)
        self.diagnostics = _Diagnostics(self, parts.diagnostics)
        self.tools = _Tools(self, parts.engine.tools)
        self.events = _Events(self, parts.events)
        self.interactions = _Interactions(self, parts.interactions)
        self.preferences = _Preferences(self, parts.preferences)
        self.commands = _Commands(
            self,
            CommandService(
                CommandServices(
                    sessions=parts.sessions,
                    conversations=parts.conversations,
                    memory=parts.memory,
                    workspace=parts.workspace,
                    providers=parts.providers,
                    diagnostics=parts.diagnostics,
                    skills=parts.skills,
                    mcp=parts.mcp,
                    preferences=parts.preferences,
                    status=self.status,
                    emit_change=self._emit_change,
                )
            ),
        )

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
        if was_created and self._parts.restore_provider_catalog:
            restored = await self._parts.providers.load_from_repository()
            if not restored.ok:
                assert restored.error is not None
                return KernelResult.failure(restored.error)
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

    async def capabilities(self) -> KernelCapabilities:
        """Return the capability matrix derived from the composed services."""
        matrix = await self._parts.capabilities.snapshot()
        features = tuple(
            capability.name for capability in matrix.capabilities if capability.status == "available"
        )
        limitations = tuple(
            f"{capability.name}: {note}"
            for capability in matrix.capabilities
            for note in capability.limitations
        )
        return KernelCapabilities(features=features, limitations=limitations)

    async def status(self) -> KernelStatus:
        active = await self._parts.supervisor.active()
        session_id = active[0][0] if active else None
        turn_id = active[0][1] if active else None
        workspace = await self._parts.workspace_leases.snapshot()
        preferences = await self._parts.preferences.snapshot()
        profile_id = preferences.profile_id or self._parts.engine_options.profile_id
        return KernelStatus(
            self.kernel_id,
            self.state,
            self._parts.package_version,
            self._started_at,
            workspace.root,
            workspace.revision,
            profile_id,
            session_id,
            turn_id,
            preferences.authorization_mode,
            preferences.plan_mode,
            preferences.thinking_mode,
            await self._context_stats(session_id, profile_id),
            self._lifecycle.degraded_reason,
        )

    async def _context_stats(self, active_session_id: SessionId | None, profile_id: ProfileId | None) -> ContextStats:
        """Estimate the active (or default) session context; zeros when unknowable."""

        session_id = active_session_id or self._parts.engine_options.default_session_id
        if session_id is None:
            return ContextStats(0, 0, 0.0)
        loaded = await self._parts.sessions.get(session_id)
        resolved = await self._parts.providers.resolve_profile(profile_id, "chat")
        if not loaded.ok or loaded.value is None or not resolved.ok or resolved.value is None:
            return ContextStats(0, 0, 0.0)
        used = estimate_context_tokens(loaded.value.messages)
        window = max(1, resolved.value.context_window)
        return ContextStats(used, window, min(100.0, max(0.0, used * 100.0 / window)))

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

    async def active_turns(self) -> tuple[ActiveTurn, ...]:
        """Snapshot every admitted turn across all sessions with pending interactions."""

        pairs = await self._parts.supervisor.active()
        if not pairs:
            return ()
        pending = {request.turn_id: request for request in await self._parts.interactions.pending()}
        active: list[ActiveTurn] = []
        for session_id, turn_id in pairs:
            snapshot = await self._parts.engine.get(turn_id)
            info = snapshot.value
            active.append(
                ActiveTurn(
                    turn_id,
                    session_id,
                    info.status if info is not None else TurnStatus.ACCEPTED,
                    info.phase if info is not None else None,
                    info.started_at if info is not None else None,
                    pending.get(turn_id),
                )
            )
        return tuple(active)

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

    async def _emit_change(
        self,
        event_type: EventType,
        revision: int,
        subject_id: str = "",
        summary: str = "",
    ) -> None:
        """Best-effort change notification; emission never fails a committed mutation."""

        if self.state in {LifecycleState.STOPPING, LifecycleState.STOPPED}:
            return
        with suppress(RuntimeError):
            await self._parts.events.emit(event_type, ChangeEvent(revision, subject_id, summary))

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
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.create(name, messages, session_id=session_id)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.SESSION_CHANGED,
                self._service.revision,
                str(result.value.session_id),
                "Session created.",
            )
        return result

    async def rename(self, session_id: SessionId, name: str) -> KernelResult[SessionRecord]:
        error = self._kernel._mutation_error("session.rename")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.rename(session_id, name)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.SESSION_CHANGED,
                self._service.revision,
                str(result.value.session_id),
                "Session renamed.",
            )
        return result

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        error = self._kernel._mutation_error("session.delete")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.delete(session_id)
        if result.ok and result.value:
            await self._kernel._emit_change(
                EventType.SESSION_CHANGED,
                self._service.revision,
                str(session_id),
                "Session deleted.",
            )
        return result

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
        result = await call()
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.SESSION_CHANGED,
                self._service.revision,
                str(result.value.session_id),
                f"{operation} committed.",
            )
        return result


class _Memory:
    def __init__(self, kernel: KairoKernel, service: MemoryService) -> None:
        self._kernel, self._service = kernel, service

    async def search(self, query: MemoryQuery) -> KernelResult[tuple[MemoryEntry, ...]]:
        return await self._service.search(query)

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        return await self._service.get(memory_id)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        error = self._kernel._mutation_error("memory.put")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.put(entry)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.MEMORY_CHANGED,
                self._service.revision,
                str(result.value.memory_id),
                "Memory entry saved.",
            )
        return result

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        error = self._kernel._mutation_error("memory.delete")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.delete(memory_id)
        if result.ok and result.value:
            await self._kernel._emit_change(
                EventType.MEMORY_CHANGED,
                self._service.revision,
                str(memory_id),
                "Memory entry deleted.",
            )
        return result


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
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.patch(patch)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.CONFIG_CHANGED, result.value.revision, "configuration", "Configuration patched."
            )
        return result

    async def backup(self) -> KernelResult[ConfigBackup]:
        error = self._kernel._mutation_error("config.backup")
        return KernelResult.failure(error) if error is not None else await self._service.backup()

    async def import_json(self, payload: str, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        error = self._kernel._mutation_error("config.import")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.import_json(payload, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.CONFIG_CHANGED, result.value.revision, "configuration", "Configuration imported."
            )
        return result

    async def restore(self, backup: ConfigBackup, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        error = self._kernel._mutation_error("config.restore")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.restore(backup, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.CONFIG_CHANGED, result.value.revision, "configuration", "Configuration restored."
            )
        return result


class _Workspace:
    def __init__(self, kernel: KairoKernel, service: WorkspaceService) -> None:
        self._kernel, self._service = kernel, service

    async def snapshot(self) -> WorkspaceState:
        return await self._service.snapshot()

    async def preview(self, relative_path: str = ".") -> KernelResult[WorkspacePreview]:
        return await self._service.preview(relative_path)

    async def tree(self, relative_path: str = ".", *, limit: int = 200) -> KernelResult[WorkspaceTree]:
        return await self._service.tree(relative_path, limit=limit)

    async def changed_files(self) -> KernelResult[ChangedFiles]:
        return await self._service.changed_files()

    async def diff(self, relative_path: str, *, max_bytes: int = 65_536) -> KernelResult[WorkspaceDiff]:
        return await self._service.diff(relative_path, max_bytes=max_bytes)

    async def move(self, target: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.move")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.move(target, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.WORKSPACE_CHANGED, result.value.revision, result.value.root, "Workspace moved."
            )
        return result

    async def save_bookmark(self, bookmark: WorkspaceBookmark, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.bookmark.save")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.save_bookmark(bookmark.name, bookmark.path, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.WORKSPACE_CHANGED, result.value.revision, result.value.root, "Workspace bookmarks updated."
            )
        return result

    async def remove_bookmark(self, name: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        error = self._kernel._mutation_error("workspace.bookmark.remove")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.remove_bookmark(name, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.WORKSPACE_CHANGED, result.value.revision, result.value.root, "Workspace bookmarks updated."
            )
        return result


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
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.store_secret(secret)
        if result.ok and result.value is not None:
            snapshot = await self._service.snapshot()
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED,
                snapshot.revision,
                str(result.value.secret_id),
                "Provider secret stored.",
            )
        return result

    async def create_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.create")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.create_profile(profile, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED, result.value.revision, "provider-catalog", "Provider profile created."
            )
        return result

    async def update_profile(
        self,
        profile: ProviderProfile,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.update")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.update_profile(profile, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED, result.value.revision, "provider-catalog", "Provider profile updated."
            )
        return result

    async def delete_profile(
        self,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.delete")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.delete_profile(profile_id, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED, result.value.revision, "provider-catalog", "Provider profile deleted."
            )
        return result

    async def map_role(
        self,
        role: str,
        profile_id: ProfileId,
        expected_revision: int,
    ) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.role.map")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.map_role(role, profile_id, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED, result.value.revision, "provider-catalog", "Provider role mapped."
            )
        return result

    async def unmap_role(self, role: str, expected_revision: int) -> KernelResult[ProviderCatalogSnapshot]:
        error = self._kernel._mutation_error("provider.role.unmap")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.unmap_role(role, expected_revision)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED, result.value.revision, "provider-catalog", "Provider role unmapped."
            )
        return result

    async def delete_secret(self, reference: SecretRef) -> KernelResult[bool]:
        error = self._kernel._mutation_error("provider.secret.delete")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.delete_secret(reference)
        if result.ok and result.value:
            snapshot = await self._service.snapshot()
            await self._kernel._emit_change(
                EventType.PROVIDER_CHANGED,
                snapshot.revision,
                str(reference.secret_id),
                "Provider secret deleted.",
            )
        return result


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
        inventory = await self._registry.reload()
        await self._kernel._emit_change(
            EventType.SKILLS_CHANGED,
            self._registry.revision,
            inventory.digest[:12],
            f"Skills reloaded ({inventory.status}).",
        )
        return KernelResult.success(inventory)

    async def trust(self, expected_digest: str) -> KernelResult[SkillInventory]:
        error = self._kernel._mutation_error("skills.trust")
        if error is not None:
            return KernelResult.failure(error)
        inventory = await self._registry.trust(expected_digest)
        await self._kernel._emit_change(
            EventType.SKILLS_CHANGED,
            self._registry.revision,
            inventory.digest[:12],
            f"Skills trusted ({inventory.status}).",
        )
        return KernelResult.success(inventory)

    async def revoke(self) -> KernelResult[bool]:
        error = self._kernel._mutation_error("skills.revoke")
        if error is not None:
            return KernelResult.failure(error)
        revoked = await self._registry.revoke()
        await self._kernel._emit_change(
            EventType.SKILLS_CHANGED, self._registry.revision, "", "Skill trust revoked."
        )
        return KernelResult.success(revoked)


class _Mcp:
    def __init__(
        self,
        kernel: KairoKernel,
        hub: McpHub,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._kernel, self._hub = kernel, hub
        self._timeout_seconds = max(0.001, timeout_seconds)

    def catalog(self) -> tuple[CatalogEntry, ...]:
        return self._hub.catalog()

    async def connect(self) -> KernelResult[tuple[McpCatalog, ...]]:
        error = self._kernel._mutation_error("mcp.connect")
        if error is not None:
            return KernelResult.failure(error)
        try:
            catalogs = await self._hub.connect_all()
        except Exception as exc:
            return KernelResult.failure(_mcp_error(exc, "mcp.connect"))
        return KernelResult.success(catalogs)

    async def refresh(self) -> KernelResult[tuple[McpCatalog, ...]]:
        error = self._kernel._mutation_error("mcp.refresh")
        if error is not None:
            return KernelResult.failure(error)
        try:
            catalogs = await self._hub.refresh_all()
        except Exception as exc:
            return KernelResult.failure(_mcp_error(exc, "mcp.refresh"))
        return KernelResult.success(catalogs)

    async def call_tool(self, qualified_name: str, arguments: JsonObject = JsonObject()) -> KernelResult[JsonObject]:
        error = self._kernel._mutation_error("mcp.tool.call")
        if error is not None:
            return KernelResult.failure(error)
        client = self._client_for(qualified_name, "tools")
        if client is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Unknown MCP tool: {qualified_name}", operation="mcp.tool.call")
            )
        authorized = await self._authorize("mcp.tool.call", OperationScope.EXTERNAL)
        if not authorized.ok:
            assert authorized.error is not None
            return KernelResult.failure(authorized.error)
        payload = thaw_json(arguments)
        try:
            result = await asyncio.wait_for(
                client.call_tool(qualified_name, payload if isinstance(payload, dict) else {}),
                self._timeout_seconds,
            )
        except TimeoutError:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    f"MCP tool call timed out after {self._timeout_seconds:g}s.",
                    retryable=True,
                    operation="mcp.tool.call",
                )
            )
        except Exception as exc:
            return KernelResult.failure(_mcp_error(exc, "mcp.tool.call"))
        return _freeze_mcp_result(result, "mcp.tool.call")

    async def read_resource(self, qualified_name: str) -> KernelResult[JsonObject]:
        error = self._kernel._read_error("mcp.resource.read")
        if error is not None:
            return KernelResult.failure(error)
        client = self._client_for(qualified_name, "resources")
        if client is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Unknown MCP resource: {qualified_name}", operation="mcp.resource.read")
            )
        authorized = await self._authorize("mcp.resource.read", OperationScope.EXTERNAL)
        if not authorized.ok:
            assert authorized.error is not None
            return KernelResult.failure(authorized.error)
        try:
            result = await asyncio.wait_for(client.read_resource(qualified_name), self._timeout_seconds)
        except TimeoutError:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    f"MCP resource read timed out after {self._timeout_seconds:g}s.",
                    retryable=True,
                    operation="mcp.resource.read",
                )
            )
        except Exception as exc:
            return KernelResult.failure(_mcp_error(exc, "mcp.resource.read"))
        return _freeze_mcp_result(result, "mcp.resource.read")

    async def render_prompt(self, qualified_name: str, arguments: JsonObject = JsonObject()) -> KernelResult[JsonObject]:
        error = self._kernel._read_error("mcp.prompt.render")
        if error is not None:
            return KernelResult.failure(error)
        client = self._client_for(qualified_name, "prompts")
        if client is None:
            return KernelResult.failure(
                KernelError(ErrorCode.NOT_FOUND, f"Unknown MCP prompt: {qualified_name}", operation="mcp.prompt.render")
            )
        authorized = await self._authorize("mcp.prompt.render", OperationScope.EXTERNAL)
        if not authorized.ok:
            assert authorized.error is not None
            return KernelResult.failure(authorized.error)
        payload = thaw_json(arguments)
        try:
            result = await asyncio.wait_for(
                client.get_prompt(qualified_name, payload if isinstance(payload, dict) else {}),
                self._timeout_seconds,
            )
        except TimeoutError:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    f"MCP prompt render timed out after {self._timeout_seconds:g}s.",
                    retryable=True,
                    operation="mcp.prompt.render",
                )
            )
        except Exception as exc:
            return KernelResult.failure(_mcp_error(exc, "mcp.prompt.render"))
        return _freeze_mcp_result(result, "mcp.prompt.render")

    async def _authorize(self, operation: str, scope: OperationScope) -> KernelResult[bool]:
        """Resolve the runtime mode and require approval whenever policy denies.

        The facade is outside any turn, so approval uses a synthetic interaction
        identity on the shared broker; the broker correlates responses by
        interaction_id/turn_id and fails closed (safe default REJECT) on expiry,
        shutdown or cancellation.
        """
        parts = self._kernel._parts
        snapshot = await parts.preferences.snapshot()
        mode = snapshot.authorization_mode
        if await parts.engine.authorization.is_authorized(mode, scope):
            return KernelResult.success(True)
        request = InteractionRequest(
            InteractionId(uuid.uuid4().hex),
            TurnId(uuid.uuid4().hex),
            SessionId(uuid.uuid4().hex),
            InteractionKind.TOOL_APPROVAL,
            f"Authorize MCP {scope.value} operation?",
            (
                InteractionChoice(InteractionAction.APPROVE_ONCE, "Run once"),
                InteractionChoice(InteractionAction.REJECT, "Reject"),
                InteractionChoice(
                    InteractionAction.ENABLE_YOLO if mode is AuthorizationMode.AUTO else InteractionAction.ENABLE_AUTO,
                    "Enable broader authorization",
                ),
            ),
            datetime.now(timezone.utc)
            + timedelta(seconds=max(0.0, parts.engine_options.interaction_timeout_seconds)),
            InteractionAction.REJECT,
        )
        source = CancellationSource()
        await parts.events.emit(EventType.INTERACTION, InteractionEvent("requested", request=request))
        try:
            response = await parts.interactions.request(request, source.token)
        finally:
            source.cancel()
        await parts.events.emit(EventType.INTERACTION, InteractionEvent("resolved", response=response))
        if response.action is InteractionAction.APPROVE_ONCE:
            return KernelResult.success(True)
        if response.action in (InteractionAction.ENABLE_AUTO, InteractionAction.ENABLE_YOLO):
            applied_mode = (
                AuthorizationMode.AUTO
                if response.action is InteractionAction.ENABLE_AUTO
                else AuthorizationMode.YOLO
            )
            applied = await parts.preferences.apply_authorization(applied_mode)
            if applied.ok and applied.value is not None:
                await parts.events.emit(
                    EventType.CONFIG_CHANGED,
                    ChangeEvent(
                        applied.value.revision,
                        "preferences",
                        f"Authorization mode is now {applied_mode.value}.",
                    ),
                )
            return KernelResult.success(True)
        return KernelResult.failure(
            KernelError(
                ErrorCode.POLICY_DENIED,
                f"{mode.value} mode did not authorize {scope.value} scope; MCP operation rejected.",
                operation=operation,
            )
        )

    def _client_for(self, qualified_name: str, namespace: str) -> McpClient | None:
        for client in self._hub.clients:
            if namespace == "tools":
                entries = client.catalog.tools
            elif namespace == "resources":
                entries = client.catalog.resources
            else:
                entries = client.catalog.prompts
            if any(entry.qualified_name == qualified_name for entry in entries):
                return client
        return None


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


class _Preferences:
    def __init__(self, kernel: KairoKernel, service: PreferencesService) -> None:
        self._kernel, self._service = kernel, service

    async def snapshot(self) -> PreferencesSnapshot:
        return await self._service.snapshot()

    async def patch(self, patch: PreferencesPatch) -> KernelResult[PreferencesSnapshot]:
        error = self._kernel._mutation_error("preferences.patch")
        if error is not None:
            return KernelResult.failure(error)
        result = await self._service.patch(patch)
        if result.ok and result.value is not None:
            await self._kernel._emit_change(
                EventType.CONFIG_CHANGED,
                result.value.revision,
                "preferences",
                "Runtime preferences updated.",
            )
        return result


class _Commands:
    def __init__(self, kernel: KairoKernel, service: CommandService) -> None:
        self._kernel, self._service = kernel, service

    def catalog(self) -> tuple[KernelCommand, ...]:
        return self._service.catalog()

    def parse(self, text: str) -> KernelResult[ParsedCommand]:
        return self._service.parse(text)

    async def execute(self, parsed: ParsedCommand, session_id: SessionId | None = None) -> KernelResult[CommandOutcome]:
        spec = self._service.spec(parsed.name)
        if spec is not None and spec.mutates:
            error = self._kernel._mutation_error(f"command.{parsed.name}")
            if error is not None:
                return KernelResult.failure(error)
        return await self._service.execute(parsed, session_id)


def _mcp_error(exc: Exception, operation: str) -> KernelError:
    if isinstance(exc, McpTrustError):
        return KernelError(ErrorCode.UNAUTHORIZED, str(exc), operation=operation)
    if isinstance(exc, McpProtocolError):
        return KernelError(ErrorCode.PROVIDER_CLIENT, str(exc), operation=operation)
    if isinstance(exc, McpError):
        return KernelError(ErrorCode.PROVIDER_CLIENT, str(exc), retryable=True, operation=operation)
    if isinstance(exc, (ConnectionError, OSError)):
        return KernelError(
            ErrorCode.PROVIDER_CONNECTION, "MCP transport failed.", retryable=True, operation=operation
        )
    return KernelError(ErrorCode.INTERNAL, "MCP operation failed.", operation=operation)


def _freeze_mcp_result(result: dict[str, object], operation: str) -> KernelResult[JsonObject]:
    try:
        frozen = freeze_json(result)
    except TypeError:
        return KernelResult.failure(
            KernelError(ErrorCode.PROVIDER_CLIENT, "MCP result is not JSON-compatible.", operation=operation)
        )
    if not isinstance(frozen, JsonObject):
        return KernelResult.failure(
            KernelError(ErrorCode.PROVIDER_CLIENT, "MCP result must be an object.", operation=operation)
        )
    return KernelResult.success(frozen)
