# Contracts, ports, and errors

## Contract rules

Public contract DTOs are frozen dataclasses derived from `Contract`. Identifiers
(`KernelId`, `TurnId`, `SessionId`, `MessageId`, `ToolCallId`, `InteractionId`,
`EventId`, `ProfileId`, `ResourceId`, `MemoryId`, `SecretId`, `TraceId`, and
`SpanId`) are opaque string `NewType`s. Callers must not infer structure from
their current generated values.

`KernelResult[T]` contains exactly one of `value` or `error`; constructing a
result with both or neither raises `ValueError`. Expected operational failures
travel as `KernelError`. Exceptions remain possible for programmer errors,
closed subscriptions, policy helper misuse, transport/protocol failures below
the façade, and dependency implementations that violate their ports.

## Content and message model

`Message` has an ID, `MessageRole`, `MessageKind`, a tuple of blocks, and an
optional name. `ContentBlock` is the closed union:

| Block | Principal fields |
|---|---|
| `TextBlock` | text |
| `ReasoningBlock` | text, redacted |
| `ImageBlock` | media type, URI/base64, alt text |
| `AudioBlock` | media type, URI/base64, transcript |
| `FileBlock` | name, media type, URI, optional size, SHA-256 |
| `ResourceBlock` | resource ID, URI, name, description, media type |
| `ToolCallBlock` | tool-call ID, name, immutable JSON arguments |
| `ToolResultBlock` | tool-call ID, name, execution status, nested content |

## Principal DTO families

- lifecycle: `KernelCapabilities`, `ContextStats`, `KernelStatus`,
  `ShutdownRequest`, `ShutdownReport`
- turn: `TurnRequest`, `TurnAccepted`, `TurnSnapshot`, `TurnResult`,
  `CancelReceipt`
- interaction: `InteractionChoice`, `InteractionRequest`,
  `InteractionResponse`, `InteractionReceipt`
- provider: `ProviderProfile`, `ProviderRequest`, `ProviderUsage`,
  `ProviderFailure`, `ProviderStreamEvent`
- tools: `ToolDescriptor`, `ToolInvocation`, `ToolExecutionContext`,
  `ToolOutputChunk`, `ToolResult`
- persistence/support: `SessionRecord`, `SessionSummary`, `ConfigSnapshot`,
  `WorkspaceRecord`, memory/resource/prompt/secret and telemetry DTOs
- events: typed payloads, `KernelEvent`, and `EventReplay`

Service-layer immutable dataclasses such as `ConfigPatch`, `WorkspaceState`,
`ProviderCatalogSnapshot`, diagnostics, capabilities, skills, and MCP catalogs
are Python API types but do **not** derive from `Contract` and therefore do not
have `to_json`/`from_json`.

## Immutable JSON

Contract fields never use a raw mapping. `JsonObject` is an ordered tuple of
`JsonMember`; `JsonArray` is a tuple of `JsonValue`. Use `freeze_json` to convert
ordinary JSON-compatible objects and `thaw_json` to convert back. Duplicate keys
can technically be represented by `JsonObject`; application schemas should
reject them when uniqueness matters.

## Tagged wire JSON

`Contract.to_json()` is deterministic (`sort_keys=True`, compact separators) and
uses these markers:

- a contract object has `$type` equal to its fully-qualified Python class name;
- a `datetime` is `{"$datetime":"<ISO-8601>"}`;
- a tuple is `{"$tuple":[...]}`;
- an immutable object is `{"$json_object":[[key,value],...]}`;
- an immutable array is `{"$json_array":[...]}`;
- string enums serialize as their string values;
- fields tagged as secret serialize as `[REDACTED]` and deserialize to an empty
  value rather than reconstructing a secret.

This is not plain dataclass JSON. Consumers must validate the tagged form in
`schema/contracts-v1.json` and `schema/events-v1.json`, or use the Python
`from_json` methods. Unknown `$type` or enum tags fail closed.

## Ports

Ports are structural `Protocol`s and all I/O methods are asynchronous:

- control: cancellation, event subscription, turn and lifecycle ports;
- interactions: request/respond;
- providers: profile resolution, streaming, probe;
- repositories: session, configuration, workspace;
- services: memory, secret, resource, prompt, observability;
- tools: authorization policy, output sink, tool, registry.

The concrete `KairoKernel` façade is the supported embedding surface. It does not
currently structurally implement every method/return annotation in
`KernelLifecyclePort`; see [limitations](limitations.md).

## Error codes

| Code | Meaning / common source |
|---|---|
| `invalid_argument` | malformed DTO input or method argument |
| `not_found` | generic missing object |
| `conflict` | revision, duplicate, terminal interaction, or state conflict |
| `unauthorized` | authentication/authorization boundary |
| `policy_denied` | path, command, network, or tool policy |
| `kernel_not_running` | façade read/mutation before start |
| `kernel_busy` | second turn in a session or mutation of an active session |
| `kernel_closing` | admission/read after shutdown begins |
| `kernel_degraded` | mutation refused or rollback recovery incomplete |
| `turn_not_found` | unknown turn ID |
| `interaction_not_found` | unknown interaction ID |
| `interaction_expired` | response arrived after expiry |
| `session_not_found` | unknown session ID |
| `session_persistence_failed` | session repository failure |
| `config_invalid` | schema/type/configuration validation failure |
| `config_persistence_failed` | config/catalog persistence failure |
| `workspace_invalid` | missing, non-directory, non-writable, or escaping path |
| `runtime_sync_failed` | transactional service participant failed |
| `provider_auth` | provider 401/403 or equivalent |
| `provider_rate_limit` | provider 429; normally retryable |
| `provider_server` | provider 5xx; normally retryable |
| `provider_connection` | provider transport failure; normally retryable |
| `provider_context` | context-window/token-limit rejection |
| `provider_client` | other provider 4xx/protocol/client failure |
| `tool_not_found` | registry has no tool with that immutable name |
| `tool_arguments_invalid` | tool arguments do not match expectations |
| `tool_execution_failed` | tool adapter/process failure |
| `tool_rejected` | authorization interaction/policy rejected execution |
| `resource_exhausted` | bounded wait timeout or resource/output limit |
| `shutdown_timeout` | shutdown hook exceeded its bound |
| `internal` | unexpected implementation failure |

`KernelError` also carries `retryable`, `operation`, immutable `details`, and
optional turn/interaction correlation IDs. Frontends should branch on `code`,
not parse human-readable messages.
