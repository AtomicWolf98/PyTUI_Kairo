"""Deterministic construction of the complete Kairo Kernel graph."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from kairo_kernel._version import __version__
from kairo_kernel.contracts.identifiers import KernelId, ProfileId, SecretId, SessionId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.lifecycle import KERNEL_API_VERSION
from kairo_kernel.contracts.preferences import PreferencesSnapshot
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import ConfigSnapshot, SecretDescriptor, SecretInput
from kairo_kernel.engine import EngineOptions, TurnEngine
from kairo_kernel.errors import KernelResult
from kairo_kernel.kernel import KairoKernel, _KernelParts
from kairo_kernel.mcp import McpClient, McpHub, McpServerConfig, McpServerTrustStore
from kairo_kernel.memory import SQLiteMemoryStore
from kairo_kernel.ports.providers import ProviderPort
from kairo_kernel.ports.repositories import ConfigRepositoryPort, SessionRepositoryPort, WorkspaceRepositoryPort
from kairo_kernel.ports.services import MemoryPort, SecretPort
from kairo_kernel.ports.tools import AuthorizationPolicyPort, ToolRegistryPort
from kairo_kernel.providers import ProviderRouter, RouterProbe, SecretResolver
from kairo_kernel.runtime import EventBus, InteractionBroker, SessionTurnSupervisor, WorkspaceLeaseManager
from kairo_kernel.services import (
    Capability,
    CapabilityService,
    ConfigurationService,
    ConversationService,
    DiagnosticDependencies,
    DiagnosticService,
    MemoryService,
    PreferencesService,
    SessionService,
    WorkspaceService,
)
from kairo_kernel.services.configuration import ConfigSchema
from kairo_kernel.services.providers import (
    InMemoryProviderCatalog,
    ProviderCatalogRepository,
    ProviderCatalogSnapshot,
    ProviderRoleMapping,
    ProviderService,
)
from kairo_kernel.skills import SkillRegistry, SkillTrustStore
from kairo_kernel.storage import (
    SQLiteConfigRepository,
    SQLiteDatabase,
    SQLiteSessionRepository,
    SQLiteWorkspaceRepository,
)
from kairo_kernel.tools import (
    AuthorizationPolicy,
    BuiltinToolRegistry,
    CompositeToolRegistry,
    ListDirTool,
    McpToolRegistry,
    PatchFileTool,
    ReadFileTool,
    RunCommandTool,
    RunPythonCodeTool,
    SearchFileTool,
    WebFetchTool,
    WriteFileTool,
)


@dataclass(frozen=True)
class KernelConfig:
    """Immutable construction settings; no credentials are stored here."""

    workspace_root: str
    database_path: str = ".kairo/kernel.db"
    kernel_id: KernelId | None = None
    package_version: str = __version__
    profiles: tuple[ProviderProfile, ...] = ()
    provider_roles: tuple[ProviderRoleMapping, ...] = ()
    default_profile_id: ProfileId | None = None
    default_session_id: SessionId | None = None
    config_values: JsonObject = JsonObject()
    config_schema: ConfigSchema = ConfigSchema(())
    engine_options: EngineOptions = EngineOptions()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    connect_mcp_on_start: bool = False
    skills_directory: str = ".kairo/skills"
    trust_directory: str = ".kairo/trust"
    event_buffer_size: int = 1000
    event_queue_size: int = 256
    shutdown_timeout_seconds: float = 5.0
    enable_builtin_tools: bool = True
    mcp_call_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.workspace_root.strip():
            raise ValueError("workspace_root is required.")
        if not self.database_path.strip():
            raise ValueError("database_path is required.")
        if self.event_buffer_size < 1 or self.event_queue_size < 1:
            raise ValueError("Event buffer sizes must be positive.")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive.")
        if self.mcp_call_timeout_seconds <= 0:
            raise ValueError("mcp_call_timeout_seconds must be positive.")


@dataclass(frozen=True)
class KernelDependencies:
    """Typed test/embedding overrides for composition boundaries."""

    provider: ProviderPort | None = None
    provider_catalog: ProviderCatalogRepository | None = None
    tools: ToolRegistryPort | None = None
    authorization: AuthorizationPolicyPort | None = None
    sessions: SessionRepositoryPort | None = None
    configuration: ConfigRepositoryPort | None = None
    workspace: WorkspaceRepositoryPort | None = None
    memory: MemoryPort | None = None
    secrets: SecretPort | None = None
    database: SQLiteDatabase | None = None
    skills: SkillRegistry | None = None
    mcp: McpHub | None = None
    diagnostics: DiagnosticService | None = None


def build_kernel(config: KernelConfig, dependencies: KernelDependencies | None = None) -> KairoKernel:
    """Build without performing I/O; call ``start`` or use ``async with``."""

    overrides = dependencies or KernelDependencies()
    root = str(Path(config.workspace_root).expanduser().resolve())
    database_path = config.database_path
    if database_path != ":memory:" and not Path(database_path).is_absolute():
        database_path = str(Path(root, database_path))
    database = overrides.database or SQLiteDatabase(database_path)
    sessions = overrides.sessions or SQLiteSessionRepository(database)
    config_repository = overrides.configuration or SQLiteConfigRepository(database)
    workspace_repository = overrides.workspace or SQLiteWorkspaceRepository(database)
    memory_port = overrides.memory or SQLiteMemoryStore(database)
    secrets = overrides.secrets or _MemorySecrets()

    leases = WorkspaceLeaseManager(root)
    supervisor = SessionTurnSupervisor()
    interactions = InteractionBroker()
    kernel_id = config.kernel_id or KernelId(_identifier())
    events = EventBus(kernel_id, config.event_buffer_size, config.event_queue_size)

    catalog = ProviderCatalogSnapshot(0, config.profiles, config.provider_roles)
    catalog_repository = overrides.provider_catalog or InMemoryProviderCatalog(catalog)
    provider_service = ProviderService(catalog_repository, secrets, (), catalog)
    if overrides.provider is None:
        router = ProviderRouter(provider_service.snapshot, _PortSecretResolver(secrets))
        probe = RouterProbe(router)
        for provider_kind in ("openai_responses", "openai_chat", "anthropic"):
            provider_service.register_probe(provider_kind, probe)
        provider: ProviderPort = router
    else:
        provider = overrides.provider
    trust_root = Path(root, config.trust_directory)
    mcp = overrides.mcp or McpHub(
        tuple(
            McpClient(server, McpServerTrustStore(trust_root / "mcp.json"))
            for server in config.mcp_servers
        )
    )
    tools = overrides.tools or CompositeToolRegistry(
        (_tools(leases, config.enable_builtin_tools), McpToolRegistry(mcp))
    )
    authorization = overrides.authorization or AuthorizationPolicy()
    engine_options = replace(
        config.engine_options,
        default_session_id=config.default_session_id,
        profile_id=config.default_profile_id,
        workspace_root=root,
        workspace_revision=0,
    )
    preferences = PreferencesService(
        PreferencesSnapshot(
            0,
            engine_options.authorization_mode,
            engine_options.plan_mode,
            engine_options.thinking_mode,
            engine_options.context_trigger_percent,
            engine_options.context_target_percent,
            engine_options.preserve_recent_turns,
            engine_options.profile_id,
        )
    )
    engine = TurnEngine(
        provider=provider,
        tools=tools,
        sessions=sessions,
        events=events,
        interactions=interactions,
        authorization=authorization,
        options=engine_options,
        supervisor=supervisor,
        preferences=preferences,
        workspace_leases=leases,
    )

    session_service = SessionService(sessions, supervisor)
    conversation_service = ConversationService(sessions, supervisor)
    memory_service = MemoryService(memory_port)
    configuration_service = ConfigurationService(
        config_repository,
        ConfigSnapshot(0, config.config_values, redacted=False),
        config.config_schema,
    )
    workspace_service = WorkspaceService(workspace_repository, leases, active_turns=supervisor.active)
    diagnostics = overrides.diagnostics or DiagnosticService(DiagnosticDependencies())

    skills = overrides.skills or SkillRegistry(Path(root), config.skills_directory, SkillTrustStore(trust_root / "skills.json"))

    capabilities = _capabilities(config)
    return KairoKernel(
        _KernelParts(
            kernel_id=kernel_id,
            package_version=config.package_version,
            shutdown_timeout_seconds=config.shutdown_timeout_seconds,
            connect_mcp_on_start=config.connect_mcp_on_start,
            database=database,
            events=events,
            interactions=interactions,
            supervisor=supervisor,
            workspace_leases=leases,
            engine=engine,
            sessions=session_service,
            conversations=conversation_service,
            memory=memory_service,
            configuration=configuration_service,
            workspace=workspace_service,
            providers=provider_service,
            skills=skills,
            mcp=mcp,
            diagnostics=diagnostics,
            engine_options=engine_options,
            capabilities=capabilities,
            preferences=preferences,
            mcp_call_timeout_seconds=config.mcp_call_timeout_seconds,
            restore_provider_catalog=overrides.provider_catalog is not None,
        )
    )


def _capabilities(config: KernelConfig) -> CapabilityService:
    """Build an honest capability matrix from the actual composition.

    Baseline services are always assembled by the factory; the provider
    and MCP integrations reflect the configured profiles and servers.
    Limitations document known approximations so consumers do not
    over-trust the matrix.
    """
    providers = (
        Capability("providers", "integration", "available", ("resolve", "stream", "probe"))
        if config.profiles
        else Capability(
            "providers",
            "integration",
            "degraded",
            ("resolve", "stream", "probe"),
            ("No provider profiles are configured.",),
        )
    )
    mcp = (
        Capability(
            "mcp",
            "integration",
            "available",
            ("connect", "catalog", "call", "read", "render", "close"),
        )
        if config.mcp_servers
        else Capability("mcp", "integration", "unavailable", (), ("No MCP servers are configured.",))
    )
    baseline = (
        Capability("turns", "agent", "available", ("run", "cancel", "status", "events", "active")),
        Capability("interactions", "agent", "available", ("approve", "reject", "expire")),
        Capability("commands", "agent", "available", ("catalog", "parse", "execute")),
        Capability("preferences", "operations", "available", ("snapshot", "patch")),
        Capability("sessions", "persistence", "available", ("create", "list", "read", "rename", "delete")),
        Capability("conversations", "persistence", "available", ("history", "clear", "undo", "compress")),
        Capability("memory", "persistence", "available", ("search", "get", "put", "delete")),
        Capability(
            "configuration",
            "operations",
            "available",
            ("snapshot", "validate", "patch", "backup", "restore"),
        ),
        Capability(
            "workspace",
            "workspace",
            "available",
            ("inspect", "switch", "snapshot", "tree", "changed_files", "diff"),
        ),
        Capability("tools", "extension", "available", ("list", "classify", "execute", "reload")),
        Capability("skills", "extension", "available", ("inspect", "trust", "reload", "revoke")),
        providers,
        mcp,
        Capability("diagnostics", "operations", "available", ("local", "full")),
        Capability(
            "status",
            "operations",
            "available",
            ("read",),
            ("Context stats estimate the active or default session only.",),
        ),
    )
    return CapabilityService(version=KERNEL_API_VERSION, baseline=baseline)


def _tools(workspace: WorkspaceLeaseManager, enabled: bool) -> ToolRegistryPort:
    if not enabled:
        return BuiltinToolRegistry(())
    return BuiltinToolRegistry(
        (
            ReadFileTool(workspace),
            WriteFileTool(workspace),
            ListDirTool(workspace),
            SearchFileTool(workspace),
            PatchFileTool(workspace),
            RunCommandTool(workspace),
            RunPythonCodeTool(workspace),
            WebFetchTool(workspace),
        )
    )


class _PortSecretResolver(SecretResolver):
    def __init__(self, secrets: SecretPort) -> None:
        self._secrets = secrets

    async def resolve(self, secret_id: str) -> str:
        result = await self._secrets.resolve(SecretId(secret_id))
        return result.value or ""


class _MemorySecrets:
    def __init__(self) -> None:
        self._values: dict[SecretId, str] = {}

    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
        present = secret_id in self._values
        return KernelResult.success(SecretDescriptor(secret_id, "memory", "********" if present else "", present))

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
        value = self._values.get(secret_id)
        if value is None:
            from kairo_kernel.contracts.enums import ErrorCode
            from kairo_kernel.errors import KernelError

            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "Secret was not found."))
        return KernelResult.success(value)

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
        self._values[secret.secret_id] = secret.value
        return KernelResult.success(SecretDescriptor(secret.secret_id, "memory", "********", True))

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
        return KernelResult.success(self._values.pop(secret_id, None) is not None)


def _identifier() -> str:
    from uuid import uuid4

    return uuid4().hex
