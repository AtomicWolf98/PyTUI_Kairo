# Providers, tools, MCP, and security

## Providers

Concrete adapters implement OpenAI Responses, OpenAI Chat Completions, and
Anthropic Messages behind `ProviderPort`. They normalize messages/content,
stream typed events, map HTTP/auth/rate/context failures, honor cancellation, and
perform bounded exponential retry for retryable failures. Provider URLs and
model/profile data are immutable per accepted turn.

The default factory supports zero or one provider kind. Mixing kinds raises at
build time; inject a routing `ProviderPort` for multi-provider kernels. Secrets
are resolved lazily from `SecretPort` and are never stored in `KernelConfig` or
provider state snapshots.

## Built-in tools

The default registry contains `read_file`, `write_file`, `list_dir`,
`search_file`, `patch_file`, `run_command`, `run_python_code`, and `web_fetch`.
Every call is described, classified, correlated by tool-call/session/turn ID,
bounded by cancellation and timeout, and returned as structured `ToolResult`.
Text output is capped and receives an explicit truncation marker.

Authorization is based on the classified `OperationScope`:

| Mode | internal | external | system | destructive |
|---|---:|---:|---:|---:|
| manual | prompt | prompt | prompt | prompt |
| auto | allow | prompt | prompt | prompt |
| yolo | allow | allow | allow | allow |

An explicit `approve_once` permits only the pending invocation. Rejection
produces a rejected tool result; stop cancels the turn. Policy classification is
repeated for each invocation.

### Filesystem and process boundaries

Filesystem tools resolve both ordinary paths and symlinks canonically under the
leased workspace. Escape is rejected. Writes use atomic replacement and leases;
search/list/read/patch have depth, result, size, and output bounds. Shell and
Python execution use child processes, a controlled environment allowlist,
workspace working directory, cancellation/timeout termination, and final kill
fallback. Command regex classification is defense in depth, not a shell parser;
embedders should add deny rules for their environment.

### Network boundary

`web_fetch` accepts only absolute HTTP(S), rejects URL credentials, applies host
allow/deny rules, resolves all addresses, rejects private/non-global addresses by
default, disables automatic redirects, and validates every redirect target.
Response bytes, redirects, time, and output are bounded. DNS is validated before
the underlying fetch rather than socket-pinned, so high-assurance deployments
should use an egress proxy that prevents DNS rebinding.

## MCP

`McpServerConfig` supports `stdio` and Streamable HTTP. Trust is an explicit
SHA-256 digest over command/arguments, URL, environment, allowlist, headers,
transport, and protocol version. Any configuration change invalidates trust.
Catalog names are deterministic and namespaced as
`mcp__<server>__<tools|resources|prompts>__<local>`.

For protocol `2026-07-28`, the client uses `server/discover` and protocol/method
HTTP headers. Older versions use `initialize`, `notifications/initialized`, and
optional HTTP session IDs. Catalog pagination is followed, sorted, and duplicate
qualified names are rejected. Transport/protocol failures trigger one close,
reconnect, catalog refresh, and retry for calls.

Security behavior:

- stdio passes only environment names in `environment_allowlist`; configured
  variables outside it fail before process launch;
- sampling and elicitation requested by a server are rejected; an
  `input_required` result fails;
- a server must be trusted before any transport starts;
- stdio stderr is discarded and shutdown has a bounded terminate fallback;
- HTTP headers can contain credentials and therefore must be supplied from a
  secure composition layer, not logged or rendered;
- MCP HTTP URLs do **not** currently use the built-in `NetworkPolicy`; validate
  and restrict them at configuration/deployment time.

The kernel façade currently manages connection/refresh/catalog only. Direct
`call_tool`, `read_resource`, and `get_prompt` are available on `McpClient` but
are not registered into the main `ToolRegistryPort` by `build_kernel`.

## Skills and trust

Skills are loaded from the configured workspace directory. The trust store binds
the canonical workspace and a digest of the complete directory snapshot.
Reload/active packages reflect only trusted current content; edits invalidate the
trusted digest. Manifest and path handling reject invalid/escaping content.

## Secrets, telemetry, and frontend boundary

`SecretInput.value` is excluded from repr/comparison and contract serialization
redacts secret-tagged fields. `ProviderCatalogSnapshot`, events, and probe errors
carry only secret references or sanitized messages. The factory fallback secret
store is memory-only. Production embedding should inject an OS/keyring/vault
`SecretPort` and ensure provider/MCP transport implementations never include
credentials in exception strings.

Structured observability emits deterministic JSON lines and optional OpenTelemetry
records. Callers control sinks; do not put secrets in free-form log fields,
metrics, span attributes, tool output, or MCP headers exposed to diagnostics.
No frontend framework is trusted or imported by the kernel; a frontend has only
the authority exposed through the façade and interaction responses.
