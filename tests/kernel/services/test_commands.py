from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    LifecycleState,
    MessageKind,
    MessageRole,
)
from kairo_kernel.contracts.identifiers import KernelId, MemoryId, MessageId, ProfileId, SecretId, SessionId
from kairo_kernel.contracts.lifecycle import ContextStats, KernelStatus
from kairo_kernel.contracts.preferences import PreferencesSnapshot
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import (
    MemoryEntry,
    MemoryQuery,
    SecretDescriptor,
    SecretInput,
    SessionRecord,
    SessionSummary,
    WorkspaceRecord,
)
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import McpHub
from kairo_kernel.runtime.turns import SessionTurnSupervisor
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.services.commands import CommandService, CommandServices
from kairo_kernel.services.conversations import ConversationService
from kairo_kernel.services.diagnostics import DiagnosticDependencies, DiagnosticService
from kairo_kernel.services.memory import MemoryService
from kairo_kernel.services.preferences import PreferencesService
from kairo_kernel.services.providers import InMemoryProviderCatalog, ProviderCatalogSnapshot, ProviderService
from kairo_kernel.services.sessions import SessionService
from kairo_kernel.services.workspaces import WorkspaceService
from kairo_kernel.skills import SkillRegistry, SkillTrustStore

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)
PROFILE = ProviderProfile(ProfileId("openai/gpt-test"), "GPT", "openai_chat", "gpt-test", "https://x.test/v1", 32000, 1000, 0.2)


def _record(identifier: str, turns: int = 0) -> SessionRecord:
    messages: list[Message] = [
        Message(MessageId(f"sys-{identifier}"), MessageRole.SYSTEM, MessageKind.CHAT, (TextBlock("system"),))
    ]
    for index in range(turns):
        messages.append(
            Message(MessageId(f"u-{identifier}-{index}"), MessageRole.USER, MessageKind.CHAT, (TextBlock(f"q{index}"),))
        )
        messages.append(
            Message(
                MessageId(f"a-{identifier}-{index}"), MessageRole.ASSISTANT, MessageKind.CHAT, (TextBlock(f"a{index}"),)
            )
        )
    return SessionRecord(SessionId(identifier), f"Session {identifier}", tuple(messages), NOW, NOW)


class FakeSessions:
    def __init__(self, *records: SessionRecord) -> None:
        self.records = {record.session_id: record for record in records}

    async def list(self) -> tuple[SessionSummary, ...]:
        return tuple(
            SessionSummary(record.session_id, record.name, len(record.messages), record.created_at, record.updated_at)
            for record in self.records.values()
        )

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]:
        record = self.records.get(session_id)
        if record is None:
            return KernelResult.failure(KernelError(ErrorCode.SESSION_NOT_FOUND, "missing"))
        return KernelResult.success(record)

    async def save(self, record: SessionRecord, active: bool) -> KernelResult[SessionRecord]:
        self.records[record.session_id] = record
        return KernelResult.success(record)

    async def delete(self, session_id: SessionId) -> KernelResult[bool]:
        return KernelResult.success(self.records.pop(session_id, None) is not None)


class FakeMemory:
    def __init__(self) -> None:
        self.entries: dict[MemoryId, MemoryEntry] = {}

    async def search(self, query: MemoryQuery) -> tuple[MemoryEntry, ...]:
        return tuple(entry for entry in self.entries.values() if entry.namespace == query.namespace)

    async def get(self, memory_id: MemoryId) -> KernelResult[MemoryEntry]:
        entry = self.entries.get(memory_id)
        if entry is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))
        return KernelResult.success(entry)

    async def put(self, entry: MemoryEntry) -> KernelResult[MemoryEntry]:
        self.entries[entry.memory_id] = entry
        return KernelResult.success(entry)

    async def delete(self, memory_id: MemoryId) -> KernelResult[bool]:
        return KernelResult.success(self.entries.pop(memory_id, None) is not None)


class FakeSecrets:
    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
        return KernelResult.success(SecretDescriptor(secret_id, "test", "", False))

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
        return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "missing"))

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
        return KernelResult.success(SecretDescriptor(secret.secret_id, "test", "***", True))

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
        return KernelResult.success(True)


class FakeWorkspaceRepository:
    def __init__(self, root: Path) -> None:
        self.record = WorkspaceRecord(str(root.resolve()), 0)

    async def current(self) -> WorkspaceRecord:
        return self.record

    async def validate(self, root: str) -> KernelResult[WorkspaceRecord]:
        path = Path(root).resolve()
        if not path.is_dir():
            return KernelResult.failure(KernelError(ErrorCode.WORKSPACE_INVALID, "invalid"))
        return KernelResult.success(WorkspaceRecord(str(path), self.record.revision + 1, self.record.root))

    async def apply(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.record = workspace
        return KernelResult.success(workspace)

    async def rollback(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.record = workspace
        return KernelResult.success(workspace)


async def _status() -> KernelStatus:
    return KernelStatus(
        KernelId("kernel"),
        LifecycleState.RUNNING,
        "0.0",
        None,
        "C:/root",
        0,
        None,
        None,
        None,
        AuthorizationMode.MANUAL,
        False,
        True,
        ContextStats(0, 0, 0.0),
    )


def _service(tmp_path: Path, *records: SessionRecord) -> CommandService:
    repository = FakeSessions(*records)
    supervisor = SessionTurnSupervisor()
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    services = CommandServices(
        sessions=SessionService(repository, supervisor),
        conversations=ConversationService(repository, supervisor),
        memory=MemoryService(FakeMemory()),
        workspace=WorkspaceService(FakeWorkspaceRepository(workspace_root), WorkspaceLeaseManager(str(workspace_root.resolve()))),
        providers=ProviderService(
            InMemoryProviderCatalog(), FakeSecrets(), (), ProviderCatalogSnapshot(0, (PROFILE,), ())
        ),
        diagnostics=DiagnosticService(DiagnosticDependencies()),
        skills=SkillRegistry(tmp_path, "skills", SkillTrustStore(tmp_path / "trust" / "skills.json")),
        mcp=McpHub(()),
        preferences=PreferencesService(PreferencesSnapshot(0)),
        status=_status,
    )
    return CommandService(services)


def test_catalog_lists_business_commands_only(tmp_path: Path) -> None:
    catalog = _service(tmp_path).catalog()
    names = {command.name for command in catalog}
    assert names == {
        "/new",
        "/sessions",
        "/clear",
        "/undo",
        "/compress",
        "/model",
        "/mode",
        "/workspace",
        "/status",
        "/find",
        "/export",
        "/doctor",
        "/skills",
        "/mcp",
        "/memory",
    }
    assert all(command.name.startswith("/") for command in catalog)


def test_parse_name_arguments_greedy_and_errors(tmp_path: Path) -> None:
    service = _service(tmp_path)
    parsed = service.parse("/new My Session")
    assert parsed.ok and parsed.value is not None
    assert parsed.value.name == "/new"
    assert parsed.value.arguments == ("My Session",)

    assert service.parse("hello").error is not None
    unknown = service.parse("/nope")
    assert unknown.error is not None and unknown.error.code is ErrorCode.NOT_FOUND
    missing = service.parse("/compress")
    assert missing.error is not None and missing.error.code is ErrorCode.INVALID_ARGUMENT

    mode = service.parse("/mode plan on")
    assert mode.ok and mode.value is not None and mode.value.arguments == ("plan", "on")


def test_execute_new_sessions_clear_undo_and_compress(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = _service(tmp_path, _record("one", turns=5))

        created = await service.execute(service.parse("/new Notes").value, None)
        assert created.ok and created.value is not None and created.value.session_id is not None

        listed = await service.execute(service.parse("/sessions").value, None)
        assert listed.ok and listed.value is not None and "Session one" in listed.value.message

        needs_session = await service.execute(service.parse("/clear").value, None)
        assert needs_session.error is not None and needs_session.error.code is ErrorCode.INVALID_ARGUMENT

        compressed = await service.execute(service.parse("/compress earlier context").value, SessionId("one"))
        assert compressed.ok and compressed.value is not None and "compression" in compressed.value.message
        undone = await service.execute(service.parse("/undo").value, SessionId("one"))
        assert undone.ok
        cleared = await service.execute(service.parse("/clear").value, SessionId("one"))
        assert cleared.ok

    asyncio.run(exercise())


def test_execute_model_and_mode_update_preferences(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = _service(tmp_path, _record("one"))
        model = await service.execute(service.parse(f"/model {PROFILE.profile_id}").value, None)
        assert model.ok and model.value is not None
        snapshot = await service._services.preferences.snapshot()
        assert snapshot.profile_id == PROFILE.profile_id

        auto = await service.execute(service.parse("/mode authorization auto").value, None)
        assert auto.ok
        plan = await service.execute(service.parse("/mode plan on").value, None)
        assert plan.ok
        snapshot = await service._services.preferences.snapshot()
        assert snapshot.authorization_mode is AuthorizationMode.AUTO
        assert snapshot.plan_mode is True

        bad = await service.execute(service.parse("/mode authorization yolo-swiss").value, None)
        assert bad.error is not None and bad.error.code is ErrorCode.INVALID_ARGUMENT

    asyncio.run(exercise())


def test_execute_workspace_status_find_export_doctor_skills_mcp_memory(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = _service(tmp_path, _record("one"))
        target = tmp_path / "elsewhere"
        target.mkdir()

        moved = await service.execute(service.parse(f"/workspace {target}").value, None)
        assert moved.ok and moved.value is not None and "revision 1" in moved.value.message

        status = await service.execute(service.parse("/status").value, None)
        assert status.ok and status.value is not None and "running" in status.value.message

        found = await service.execute(service.parse("/find one").value, None)
        assert found.ok and found.value is not None and "Session one" in found.value.message

        exported = await service.execute(service.parse("/export").value, SessionId("one"))
        assert exported.ok and exported.value is not None
        assert "one" in str(exported.value.data.get("payload"))

        doctor = await service.execute(service.parse("/doctor").value, None)
        assert doctor.ok and doctor.value is not None and doctor.value.message.startswith("ok")

        skills = await service.execute(service.parse("/skills").value, None)
        assert skills.ok and skills.value is not None and "untrusted" in skills.value.message

        mcp = await service.execute(service.parse("/mcp").value, None)
        assert mcp.ok and mcp.value is not None and "0 MCP server(s)" in mcp.value.message

        memory_put = await service._services.memory.put(
            MemoryEntry(MemoryId("m-1"), "user", "greeting", (TextBlock("hi"),), NOW, NOW)
        )
        assert memory_put.ok
        memory = await service.execute(service.parse("/memory user greet").value, None)
        assert memory.ok and memory.value is not None and "greeting" in memory.value.message

    asyncio.run(exercise())
