# Public Python API

## Construction

`build_kernel(config: KernelConfig, dependencies: KernelDependencies | None =
None) -> KairoKernel` is synchronous and side-effect free. `KernelDependencies`
provides typed overrides for provider, tool registry, authorization, session,
configuration, workspace, memory, secret, database, skills, MCP, and diagnostic
boundaries.

`KernelConfig` fields are:

| Field | Default / constraint |
|---|---|
| `workspace_root` | required non-empty path; resolved during build |
| `database_path` | `.kairo/kernel.db`; relative paths are rooted in the workspace; `:memory:` is supported |
| `kernel_id` | generated when absent |
| `package_version` | current distribution version (`0.4.0a2`) |
| `profiles`, `provider_roles`, `default_profile_id` | immutable provider configuration |
| `default_session_id` | none; otherwise permits `TurnRequest.session_id=None` |
| `config_values`, `config_schema` | initial configuration service state |
| `engine_options` | `EngineOptions()`; factory overwrites default session/profile/workspace fields |
| `mcp_servers`, `connect_mcp_on_start` | empty / false |
| `skills_directory`, `trust_directory` | `.kairo/skills`, `.kairo/trust` relative to workspace |
| `event_buffer_size`, `event_queue_size` | 1000 / 256; both positive |
| `shutdown_timeout_seconds` | 5.0; positive |
| `enable_builtin_tools` | true |

## Kernel lifecycle and turns

| Call | Result |
|---|---|
| `await kernel.start()` | `KernelResult[LifecycleState]`; idempotent only after reaching `running` |
| `await kernel.status()` | `KernelStatus` |
| `await kernel.submit(TurnRequest)` | `KernelResult[TurnAccepted]` |
| `await kernel.turn(turn_id)` | `KernelResult[TurnSnapshot]` |
| `await kernel.wait(turn_id, timeout_seconds=None)` | `KernelResult[TurnResult]`; timeout does not cancel the turn |
| `await kernel.cancel(turn_id, reason="")` | `KernelResult[CancelReceipt]` |
| `await kernel.shutdown(ShutdownRequest | None)` | `KernelResult[ShutdownReport]`; successful shutdown is idempotent |
| `await kernel.mark_degraded(reason)` | transitions to degraded and emits a lifecycle event |
| `await kernel.active_turns()` | `tuple[ActiveTurn, ...]` snapshot of in-flight turns: id, session, status, phase, started_at, pending interaction |
| `await kernel.capabilities()` | `KernelCapabilities` derived from the composed services; unavailable integrations are omitted from `features` and explained in `limitations` |

Before `start`, reads and mutations return `kernel_not_running`. During stopping
or after stop they return `kernel_closing`. In degraded state reads remain
available while façade mutations return `kernel_degraded`.

## Namespaced services

All mutating calls listed below are also guarded by the kernel lifecycle.

### `kernel.sessions`

- `revision` (process-local successful-mutation counter)
- `list()`, `get(session_id)`
- `create(name, messages=(), session_id=None)`
- `rename(session_id, name)`, `delete(session_id)`
- `search(text, limit=50)`
- `export(session_id, format="json" | "markdown")`

Rename and delete fail with `kernel_busy` while that session has an active turn.
There is no implicit active session in this service.

### `kernel.conversations`

- `history(session_id)`
- `clear(session_id)` preserves the leading system-message prefix
- `undo(session_id)` removes the most recent user turn and everything after it
- `compress(session_id, summary, preserve_recent_turns=4)`

Mutations fail with `kernel_busy` for an active session.

### `kernel.memory`

- `search(MemoryQuery)`, `get(memory_id)`
- `put(MemoryEntry)`, `delete(memory_id)`

The default store is namespace-scoped SQLite FTS5 with exact tag filters and a
hard search limit of 100.

### `kernel.configuration`

- `snapshot()` and `export_json()` always redact schema fields marked `secret`
- `validate(values)`
- `patch(ConfigPatch)`, `import_json(payload, expected_revision)`
- `backup()`, `restore(ConfigBackup, expected_revision)`

Mutations use optimistic `expected_revision`. Failed runtime synchronization is
rolled back; failed rollback marks the service and kernel degraded when wired to
a degraded signal.

### `kernel.preferences`

- `snapshot()`, `patch(PreferencesPatch)` (mutation-gated, optimistic
  `expected_revision`, emits a config change event)

Runtime preferences seed from `KernelConfig.engine_options`; they are
process-local and are not written back to the configuration document.

### `kernel.commands`

- `catalog()` and `parse(text)` are synchronous
- `execute(parsed, session_id=None)` is fail-closed: outcomes carry typed
  `KernelError` payloads instead of raising

Mutating commands are lifecycle-gated per `KernelCommand.mutates`.

### `kernel.workspace`

- `snapshot()`, `preview(relative_path=".")`
- `move(target, expected_revision)` — returns retryable `KERNEL_BUSY` while any turn is active
- `save_bookmark(WorkspaceBookmark, expected_revision)`
- `remove_bookmark(name, expected_revision)`
- `tree(relative_path=".", limit=200)`, `changed_files()`, `diff(relative_path, max_bytes=65536)`

Preview canonicalizes the path, rejects workspace/symlink escape, limits bytes
and directory entries, and returns a frozen `WorkspacePreview`.

### `kernel.providers`

- `snapshot()`, `resolve(profile_id=None, role="")`, `probe(profile_id)`
- `store_secret(SecretInput)`, `delete_secret(SecretRef)`
- revisioned profile `create_profile`, `update_profile`, `delete_profile`
- revisioned `map_role`, `unmap_role`

Snapshots contain only opaque secret identifiers, never secret values.

### `kernel.skills`

- `inspect()`, `active()`
- `reload()`, `trust(expected_digest)`, `revoke()`

Trust is bound to a digest of the workspace skill directory.

### `kernel.mcp`

- synchronous `catalog()`
- asynchronous `connect()` and `refresh()`
- typed `call_tool(qualified_name, arguments)`, `read_resource(qualified_name)`, `render_prompt(qualified_name, arguments)`

MCP catalog tools are bridged into the engine tool registry via
`CompositeToolRegistry`, so `kernel.tools.list()` includes MCP entries.

All three MCP calls pass through the facade ToolGate: the runtime mode is
resolved from preferences, external scope in manual/auto raises a
`TOOL_APPROVAL` interaction (approved via `kernel.interactions.respond`;
safe default reject), yolo executes directly, each call is bounded by
`KernelConfig.mcp_call_timeout_seconds`, and timeout/disconnect fail closed
as `RESOURCE_EXHAUSTED` (retryable) / `PROVIDER_CLIENT`.

### `kernel.diagnostics`, `kernel.tools`

- diagnostics: `local()`, `full()`
- tools: `list()`, `reload()`

Local diagnostics cover configured queue, worker, lease, database, replay, blob,
and memory probes. Full diagnostics also adds provider and MCP probes. Missing
probes are reported as `skipped`.

### `kernel.events`, `kernel.interactions`

- events: `snapshot(after_sequence=0, limit=1000)`,
  `subscribe(after_sequence=0, queue_size=None)`
- interactions: `pending()`, `respond(InteractionResponse)`

Frontends should keep the last global event sequence, reconnect with that value,
and refresh state if `EventReplay.gap` is true or `SubscriberOverflow` is raised.

### Configuration document and provider persistence

`KernelConfigStore(path)` (`kairo_kernel/services/config_document.py`) loads
and atomically saves the versioned global config document
(`KernelConfigDocument`: profiles, role routing, MCP servers, theme,
keybindings, recent workspaces). The kernel resolves no platformdirs path —
the embedding frontend chooses the file location.

`store.update(expected_revision, transform)` mutates the document under a
single-process lock and advances its persisted `revision`; stale writers
receive `CONFLICT`.
`DocumentProviderCatalog(store)` persists provider profile/role mutations
through that document; the default factory catalog stays in-memory unless
`KernelDependencies.provider_catalog` is supplied.

## Public bootstrap (frontends use only this)

`open_kernel(options, *, secrets=None, provider=None, tools=None)` is the one
supported way for a frontend to open a kernel; it never calls `asyncio.run`
and never reads or logs secret values.

| Type | Meaning |
|---|---|
| `KernelOpenOptions(workspace_root, config_path, safe_mode=False, package_version=None)` | everything a frontend decides before opening |
| `OpenedKernel(kernel, config_revision, config_missing, config_warning)` | started kernel plus document facts |
| `await open_kernel(...)` | `KernelResult[OpenedKernel]` |

Semantics:

- A missing config document starts an empty kernel with `config_missing=True`
  (not an error). An invalid document returns a typed failure and is never
  overwritten.
- Profiles, role mappings, MCP servers and the default profile are loaded
  from the document; in normal mode the provider catalog repository is
  `DocumentProviderCatalog` over the same document, so provider CRUD and role
  map/unmap persist to it. Safe mode uses an in-memory catalog, never
  auto-connects MCP and does not relax authorization.
- A failed `kernel.start()` shuts down every opened resource (including the
  database) before the failure is returned.

