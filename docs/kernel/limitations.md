# Known limitations of the current composition

These are implementation facts at baseline `c770dfd`, not planned behavior.

1. Workspace bookmarks default to an in-memory repository and are not
   persisted or connected to configuration by the factory.
2. The configuration service is constructed from `KernelConfig.config_values`
   at revision 0; the factory does not load/reconcile the current
   `ConfigRepositoryPort` snapshot during start.
3. The fallback secret store is memory-only. Secrets are lost when the
   process exits unless embedding supplies persistence. (Provider catalog
   mutations persist when `DocumentProviderCatalog` is wired.)
4. Event replay and interaction terminal retention are bounded memory only.
   Restart loses the cursor history; frontends need a full state refresh.
5. MCP Streamable HTTP does not apply the built-in private-network/redirect
   `NetworkPolicy`. Deployment must validate endpoints and control egress.
6. Diagnostics dependencies default to empty, so default local/full reports
   are mostly `skipped` and do not prove the configured provider/database/MCP
   is healthy.
7. `BlobStore`, resource and prompt ports, and structured observability
   exist as components but are not surfaced/wired by the default
   `KairoKernel` composition.
8. The Alpha wheel now discovers only `kairo_kernel*`; the legacy source-tree
   packages remain outside the Alpha distribution and are not migrated.
9. SQLite uses one connection and one operation lock, so it provides
   correctness and simple transactions rather than high read/write
   throughput.
10. `SkillRegistry.revision` and the provider catalog revision are
    process-local: they track in-process mutations only, not external file
    changes.
11. The configuration document lock is single-process: `KernelConfigStore`
    serializes writers in this process and rejects stale writers with
    `CONFLICT`, but concurrent writers in *separate* processes remain
    uncoordinated (atomic replace prevents torn files, not lost updates).
12. `kernel.status()` context stats estimate the **active or default
    session only** (`tokens` is `None` unless a provider wires usage
    accounting through).
13. Engine-turn MCP tool calls (`McpTool.execute`) are authorized by the
    turn engine but do not apply the facade call timeout; a hung MCP server
    blocks the turn until the transport fails or the process exits.

Frontends should not emulate missing behavior. They should feature-detect at the
composition boundary, render typed failures, refresh after replay gaps, and keep
all state changes behind optimistic revisions.
