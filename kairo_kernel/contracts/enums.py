"""Stable string enums for Kernel Contract v1."""

from kairo_kernel.contracts.json import ContractEnum


class LifecycleState(ContractEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEGRADED = "degraded"


class TurnStatus(ContractEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    STOPPING = "stopping"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnPhase(ContractEnum):
    PLANNING = "planning"
    CONNECTING = "connecting"
    THINKING = "thinking"
    STREAMING = "streaming"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING_TOOL = "running_tool"
    COMPACTING = "compacting"


class MessageRole(ContractEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageKind(ContractEnum):
    CHAT = "chat"
    PLAN = "plan"
    SUMMARY = "summary"
    RUNTIME_STATE = "runtime_state"


class AuthorizationMode(ContractEnum):
    MANUAL = "manual"
    AUTO = "auto"
    YOLO = "yolo"


class OperationScope(ContractEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    SYSTEM = "system"
    DESTRUCTIVE = "destructive"


class InteractionKind(ContractEnum):
    TOOL_APPROVAL = "tool_approval"
    PLAN_APPROVAL = "plan_approval"
    TEXT_INPUT = "text_input"


class InteractionAction(ContractEnum):
    APPROVE_ONCE = "approve_once"
    REJECT = "reject"
    STOP = "stop"
    ENABLE_AUTO = "enable_auto"
    ENABLE_YOLO = "enable_yolo"
    SUBMIT_TEXT = "submit_text"


class ToolExecutionStatus(ContractEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ProviderStreamKind(ContractEnum):
    CONTENT = "content"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    COMPLETED = "completed"
    FAILED = "failed"


class ProviderFailureKind(ContractEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    CONNECTION = "connection"
    CONTEXT = "context"
    CLIENT = "client"
    CANCELLED = "cancelled"


class EventType(ContractEnum):
    LIFECYCLE = "lifecycle"
    TURN = "turn"
    MESSAGE = "message"
    TOOL = "tool"
    INTERACTION = "interaction"
    USAGE = "usage"
    CONTEXT = "context"
    SESSION_CHANGED = "session_changed"
    CONFIG_CHANGED = "config_changed"
    WORKSPACE_CHANGED = "workspace_changed"
    SKILLS_CHANGED = "skills_changed"
    PROVIDER_CHANGED = "provider_changed"
    MEMORY_CHANGED = "memory_changed"
    NOTICE = "notice"


class ErrorCode(ContractEnum):
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED = "unauthorized"
    POLICY_DENIED = "policy_denied"
    KERNEL_NOT_RUNNING = "kernel_not_running"
    KERNEL_BUSY = "kernel_busy"
    KERNEL_CLOSING = "kernel_closing"
    KERNEL_DEGRADED = "kernel_degraded"
    TURN_NOT_FOUND = "turn_not_found"
    INTERACTION_NOT_FOUND = "interaction_not_found"
    INTERACTION_EXPIRED = "interaction_expired"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_PERSISTENCE_FAILED = "session_persistence_failed"
    CONFIG_INVALID = "config_invalid"
    CONFIG_PERSISTENCE_FAILED = "config_persistence_failed"
    WORKSPACE_INVALID = "workspace_invalid"
    RUNTIME_SYNC_FAILED = "runtime_sync_failed"
    PROVIDER_AUTH = "provider_auth"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_SERVER = "provider_server"
    PROVIDER_CONNECTION = "provider_connection"
    PROVIDER_CONTEXT = "provider_context"
    PROVIDER_CLIENT = "provider_client"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_ARGUMENTS_INVALID = "tool_arguments_invalid"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_REJECTED = "tool_rejected"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    SHUTDOWN_TIMEOUT = "shutdown_timeout"
    INTERNAL = "internal"
