"""Typed kernel business commands: catalog, parse and execute."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TypeVar

from kairo_kernel.contracts.commands import CommandArgument, CommandOutcome, KernelCommand, ParsedCommand
from kairo_kernel.contracts.enums import AuthorizationMode, ErrorCode, EventType
from kairo_kernel.contracts.identifiers import ProfileId, SessionId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.lifecycle import KernelStatus
from kairo_kernel.contracts.preferences import PreferencesPatch
from kairo_kernel.contracts.support import MemoryQuery
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.mcp import McpHub, McpProtocolError
from kairo_kernel.services.conversations import ConversationService
from kairo_kernel.services.diagnostics import DiagnosticService
from kairo_kernel.services.memory import MemoryService
from kairo_kernel.services.preferences import PreferencesService
from kairo_kernel.services.providers import ProviderService
from kairo_kernel.services.sessions import SessionService
from kairo_kernel.services.workspaces import WorkspaceService
from kairo_kernel.skills import SkillRegistry

ResultT = TypeVar("ResultT")

CATALOG: tuple[KernelCommand, ...] = (
    KernelCommand(
        "/new",
        "Create a session",
        "Create a new persisted session",
        (CommandArgument("name", greedy=True),),
        mutates=True,
    ),
    KernelCommand("/sessions", "List sessions", "List persisted sessions"),
    KernelCommand(
        "/clear",
        "Clear session history",
        "Clear the history of a session",
        (),
        mutates=True,
        needs_session=True,
    ),
    KernelCommand(
        "/undo",
        "Undo latest turn",
        "Remove the latest user turn and everything after it",
        (),
        mutates=True,
        needs_session=True,
    ),
    KernelCommand(
        "/compress",
        "Compress context",
        "Summarize older context while keeping recent turns",
        (CommandArgument("summary", required=True, greedy=True),),
        mutates=True,
        needs_session=True,
    ),
    KernelCommand(
        "/model",
        "Switch chat profile",
        "Switch the chat profile for future turns",
        (CommandArgument("profile_id", required=True),),
        mutates=True,
    ),
    KernelCommand(
        "/mode",
        "Switch mode",
        "Switch authorization, plan or thinking mode",
        (
            CommandArgument("setting", required=True, value_hint="authorization|plan|thinking"),
            CommandArgument("value", required=True),
        ),
        mutates=True,
    ),
    KernelCommand(
        "/workspace",
        "Switch workspace",
        "Move the workspace to a path or bookmark",
        (CommandArgument("path", required=True),),
        mutates=True,
    ),
    KernelCommand("/status", "Show status", "Show read-only kernel status"),
    KernelCommand(
        "/find",
        "Search sessions",
        "Search session names and content",
        (CommandArgument("text", required=True, greedy=True),),
    ),
    KernelCommand(
        "/export",
        "Export session",
        "Export a session as json or markdown",
        (CommandArgument("format", value_hint="json|markdown"),),
        needs_session=True,
    ),
    KernelCommand(
        "/doctor",
        "Run diagnostics",
        "Run local or full diagnostics",
        (CommandArgument("scope", value_hint="local|full"),),
    ),
    KernelCommand("/skills", "Inspect skills", "Show the skill inventory status"),
    KernelCommand("/mcp", "MCP catalog", "Show the MCP catalog summary"),
    KernelCommand(
        "/memory",
        "Search memory",
        "Search memory entries in a namespace",
        (CommandArgument("namespace", required=True), CommandArgument("text", greedy=True)),
    ),
)

Handler = Callable[[tuple[str, ...], SessionId | None], Awaitable[KernelResult[CommandOutcome]]]


@dataclass(frozen=True)
class CommandServices:
    sessions: SessionService
    conversations: ConversationService
    memory: MemoryService
    workspace: WorkspaceService
    providers: ProviderService
    diagnostics: DiagnosticService
    skills: SkillRegistry
    mcp: McpHub
    preferences: PreferencesService
    status: Callable[[], Awaitable[KernelStatus]]
    emit_change: Callable[[EventType, int, str, str], Awaitable[None]] | None = None


class CommandService:
    """Catalog/parse/execute over kernel services; lifecycle gating lives in the facade."""

    def __init__(self, services: CommandServices) -> None:
        self._services = services
        self._specs = {command.name: command for command in CATALOG}

    def catalog(self) -> tuple[KernelCommand, ...]:
        return CATALOG

    def spec(self, name: str) -> KernelCommand | None:
        return self._specs.get(name)

    def parse(self, text: str) -> KernelResult[ParsedCommand]:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return _failure(ErrorCode.INVALID_ARGUMENT, "Command text must start with '/'.", "command.parse")
        head, _, remainder = stripped.partition(" ")
        name = head.lower()
        spec = self._specs.get(name)
        if spec is None:
            return _failure(ErrorCode.NOT_FOUND, f"Unknown command: {name}", "command.parse")
        tokens = remainder.split()
        arguments: list[str] = []
        for index, argument in enumerate(spec.arguments):
            if argument.greedy:
                value = " ".join(tokens[index:]).strip()
                if value:
                    arguments.append(value)
                break
            if index < len(tokens):
                arguments.append(tokens[index])
        for index, argument in enumerate(spec.arguments):
            if argument.required and (index >= len(arguments) or not arguments[index]):
                return _failure(
                    ErrorCode.INVALID_ARGUMENT,
                    f"Command {name} requires argument '{argument.name}'.",
                    "command.parse",
                )
        return KernelResult.success(ParsedCommand(name, tuple(arguments)))

    async def execute(self, parsed: ParsedCommand, session_id: SessionId | None = None) -> KernelResult[CommandOutcome]:
        spec = self._specs.get(parsed.name)
        if spec is None:
            return _failure(ErrorCode.NOT_FOUND, f"Unknown command: {parsed.name}", "command.execute")
        if spec.needs_session and session_id is None:
            return _failure(
                ErrorCode.INVALID_ARGUMENT,
                f"Command {parsed.name} requires an active session.",
                "command.execute",
            )
        handler = self._handlers()[parsed.name]
        return await handler(parsed.arguments, session_id)

    async def _emit_change(self, event_type: EventType, revision: int, subject_id: str = "", summary: str = "") -> None:
        """Best-effort change notification; emission never fails a committed command."""

        sink = self._services.emit_change
        if sink is None:
            return
        with suppress(RuntimeError):
            await sink(event_type, revision, subject_id, summary)

    def _handlers(self) -> dict[str, Handler]:
        return {
            "/new": self._new,
            "/sessions": self._list_sessions,
            "/clear": self._clear,
            "/undo": self._undo,
            "/compress": self._compress,
            "/model": self._model,
            "/mode": self._mode,
            "/workspace": self._move_workspace,
            "/status": self._status,
            "/find": self._find,
            "/export": self._export,
            "/doctor": self._doctor,
            "/skills": self._skills,
            "/mcp": self._mcp,
            "/memory": self._memory,
        }

    async def _new(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        created = await self._services.sessions.create(arguments[0] if arguments else "New session")
        if created.error is not None:
            return KernelResult.failure(created.error)
        assert created.value is not None
        await self._emit_change(
            EventType.SESSION_CHANGED,
            self._services.sessions.revision,
            str(created.value.session_id),
            "Session created.",
        )
        return KernelResult.success(
            CommandOutcome("/new", f"Created session '{created.value.name}'.", created.value.session_id)
        )

    async def _list_sessions(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments, session_id
        listed = await self._services.sessions.list()
        if listed.error is not None:
            return KernelResult.failure(listed.error)
        assert listed.value is not None
        lines = [f"{item.name} ({item.session_id})" for item in listed.value]
        return KernelResult.success(CommandOutcome("/sessions", "\n".join(lines) or "No sessions."))

    async def _clear(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments
        assert session_id is not None
        cleared = await self._services.conversations.clear(session_id)
        if cleared.error is not None:
            return KernelResult.failure(cleared.error)
        if cleared.value is not None:
            await self._emit_change(
                EventType.SESSION_CHANGED,
                self._services.conversations.revision,
                str(cleared.value.session_id),
                "conversation.clear committed.",
            )
        return KernelResult.success(CommandOutcome("/clear", "Conversation cleared.", session_id))

    async def _undo(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments
        assert session_id is not None
        undone = await self._services.conversations.undo(session_id)
        if undone.error is not None:
            return KernelResult.failure(undone.error)
        if undone.value is not None:
            await self._emit_change(
                EventType.SESSION_CHANGED,
                self._services.conversations.revision,
                str(undone.value.session_id),
                "conversation.undo committed.",
            )
        return KernelResult.success(CommandOutcome("/undo", "Latest turn removed.", session_id))

    async def _compress(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        assert session_id is not None
        compressed = await self._services.conversations.compress(session_id, arguments[0])
        if compressed.error is not None:
            return KernelResult.failure(compressed.error)
        assert compressed.value is not None
        await self._emit_change(
            EventType.SESSION_CHANGED,
            self._services.conversations.revision,
            str(compressed.value.session_id),
            "conversation.compress committed.",
        )
        return KernelResult.success(
            CommandOutcome(
                "/compress",
                f"Conversation compressed; {compressed.value.compression_count} compression(s) total.",
                session_id,
            )
        )

    async def _model(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        profile_id = ProfileId(arguments[0])
        resolved = await self._services.providers.resolve_profile(profile_id)
        if resolved.error is not None:
            return KernelResult.failure(resolved.error)
        snapshot = await self._services.preferences.snapshot()
        patched = await self._services.preferences.patch(PreferencesPatch(snapshot.revision, profile_id=profile_id))
        if patched.error is not None:
            return KernelResult.failure(patched.error)
        if patched.value is not None:
            await self._emit_change(
                EventType.CONFIG_CHANGED,
                patched.value.revision,
                "preferences",
                "Runtime preferences updated.",
            )
        assert resolved.value is not None
        return KernelResult.success(
            CommandOutcome("/model", f"Chat profile is now {resolved.value.label} ({profile_id}).")
        )

    async def _mode(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        setting, value = arguments[0].lower(), arguments[1].lower()
        snapshot = await self._services.preferences.snapshot()
        if setting == "authorization":
            if value not in {"manual", "auto", "yolo"}:
                return _failure(
                    ErrorCode.INVALID_ARGUMENT, "Authorization must be manual, auto or yolo.", "command./mode"
                )
            patched = await self._services.preferences.patch(
                PreferencesPatch(snapshot.revision, authorization_mode=AuthorizationMode(value))
            )
        elif setting in {"plan", "thinking"}:
            if value not in {"on", "off"}:
                return _failure(ErrorCode.INVALID_ARGUMENT, f"{setting} must be on or off.", "command./mode")
            enabled = value == "on"
            patch = (
                PreferencesPatch(snapshot.revision, plan_mode=enabled)
                if setting == "plan"
                else PreferencesPatch(snapshot.revision, thinking_mode=enabled)
            )
            patched = await self._services.preferences.patch(patch)
        else:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Mode must be authorization, plan or thinking.", "command./mode")
        if patched.error is not None:
            return KernelResult.failure(patched.error)
        assert patched.value is not None
        await self._emit_change(
            EventType.CONFIG_CHANGED,
            patched.value.revision,
            "preferences",
            "Runtime preferences updated.",
        )
        return KernelResult.success(
            CommandOutcome("/mode", f"{setting} is now {value} (revision {patched.value.revision}).")
        )

    async def _move_workspace(
        self, arguments: tuple[str, ...], session_id: SessionId | None
    ) -> KernelResult[CommandOutcome]:
        del session_id
        state = await self._services.workspace.snapshot()
        moved = await self._services.workspace.move(arguments[0], state.revision)
        if moved.error is not None:
            return KernelResult.failure(moved.error)
        assert moved.value is not None
        await self._emit_change(
            EventType.WORKSPACE_CHANGED,
            moved.value.revision,
            moved.value.root,
            "Workspace moved.",
        )
        return KernelResult.success(
            CommandOutcome("/workspace", f"Workspace is now {moved.value.root} (revision {moved.value.revision}).")
        )

    async def _status(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments, session_id
        status = await self._services.status()
        message = (
            f"{status.state.value} · workspace {status.workspace_root} (rev {status.workspace_revision})"
            f" · {status.authorization_mode.value} · plan {'on' if status.plan_mode else 'off'}"
            f" · thinking {'on' if status.thinking_mode else 'off'}"
        )
        return KernelResult.success(CommandOutcome("/status", message, data=status.to_json_value()))

    async def _find(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        found = await self._services.sessions.search(arguments[0])
        if found.error is not None:
            return KernelResult.failure(found.error)
        assert found.value is not None
        lines = [f"{item.name} ({item.session_id})" for item in found.value]
        return KernelResult.success(CommandOutcome("/find", "\n".join(lines) or "No matches."))

    async def _export(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        assert session_id is not None
        format = arguments[0] if arguments else "json"
        exported = await self._services.sessions.export(session_id, format=format)
        if exported.error is not None:
            return KernelResult.failure(exported.error)
        assert exported.value is not None
        return KernelResult.success(
            CommandOutcome(
                "/export",
                f"Exported session as {format}.",
                session_id,
                JsonObject.from_pairs(("payload", exported.value)),
            )
        )

    async def _doctor(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        scope = arguments[0].lower() if arguments else "local"
        if scope not in {"local", "full"}:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Diagnostics scope must be local or full.", "command./doctor")
        report = await (self._services.diagnostics.full() if scope == "full" else self._services.diagnostics.local())
        ok = sum(1 for check in report.checks if check.status == "ok")
        message = f"{report.status}: {ok}/{len(report.checks)} checks ok ({report.duration_ms:.0f} ms)"
        return KernelResult.success(CommandOutcome("/doctor", message))

    async def _skills(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments, session_id
        inventory = await self._services.skills.inspect()
        digest = inventory.digest[:12] or "none"
        return KernelResult.success(
            CommandOutcome("/skills", f"Skills {inventory.status}: {len(inventory.packages)} package(s), digest {digest}.")
        )

    async def _mcp(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del arguments, session_id
        try:
            entries = self._services.mcp.catalog()
        except McpProtocolError as exc:
            return _failure(ErrorCode.CONFIG_INVALID, str(exc), "command./mcp")
        tools = sum(1 for entry in entries if entry.namespace == "tools")
        resources = sum(1 for entry in entries if entry.namespace == "resources")
        prompts = sum(1 for entry in entries if entry.namespace == "prompts")
        servers = len(self._services.mcp.clients)
        return KernelResult.success(
            CommandOutcome("/mcp", f"{servers} MCP server(s): {tools} tool(s), {resources} resource(s), {prompts} prompt(s).")
        )

    async def _memory(self, arguments: tuple[str, ...], session_id: SessionId | None) -> KernelResult[CommandOutcome]:
        del session_id
        namespace = arguments[0]
        text = arguments[1] if len(arguments) > 1 else ""
        found = await self._services.memory.search(MemoryQuery(namespace, text))
        if found.error is not None:
            return KernelResult.failure(found.error)
        assert found.value is not None
        lines = [f"{entry.key} ({entry.memory_id})" for entry in found.value]
        return KernelResult.success(CommandOutcome("/memory", "\n".join(lines) or "No memory entries."))


def _failure(code: ErrorCode, message: str, operation: str) -> KernelResult[ResultT]:
    return KernelResult.failure(KernelError(code, message, operation=operation))
