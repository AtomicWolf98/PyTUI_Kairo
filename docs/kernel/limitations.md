# Known limitations of the current composition

These are implementation facts at baseline `c770dfd`, not planned behavior.

1. `KairoKernel.start()` returns `KernelResult[LifecycleState]` and the façade has
   no `capabilities()` method, while `KernelLifecyclePort` declares start as
   `KernelResult[KernelStatus]` and declares capabilities. The concrete façade
   therefore does not structurally satisfy that whole port today.
2. `CapabilityService` exists but is not included in `_KernelParts` or exposed by
   `KairoKernel`. Its baseline matrix can overstate façade reachability (notably
   MCP call/resource/prompt operations).
3. Workspace and configuration services provide read leases for turn snapshot
   isolation, but `TurnEngine` does not acquire them. It captures build-time
   `EngineOptions.workspace_root/workspace_revision`; workspace moves do not
   update those fields for later turns. Built-in tools have their own workspace
   lease manager, but the emitted event revision/tool context can remain stale.
4. Provider/profile CRUD updates an in-memory provider catalog only. The engine's
   concrete provider adapter and `EngineOptions.profile_id` are not rebuilt or
   switched. The factory provides no `ProviderProbePort` to `ProviderService`, so
   `kernel.providers.probe()` returns not found by default even though the engine
   adapter itself supports probe.
5. Workspace bookmarks default to an in-memory repository and are not persisted
   or connected to configuration by the factory.
6. The configuration service is constructed from `KernelConfig.config_values`
   at revision 0; the factory does not load/reconcile the current
   `ConfigRepositoryPort` snapshot during start.
7. The fallback secret store and provider catalog are memory-only. Secrets,
   provider CRUD, role changes, and bookmarks are lost when the process exits
   unless embedding supplies persistence.
8. Event replay and interaction terminal retention are bounded memory only.
   Restart loses the cursor history; frontends need a full state refresh.
9. Change/notice/context event types are reserved but session, config, workspace,
   skill, and memory services do not emit them through the kernel event bus.
10. MCP catalog entries are not adapted into the engine tool/resource/prompt
    ports. The façade exposes connect/refresh/catalog but not call/read/render.
11. MCP Streamable HTTP does not apply the built-in private-network/redirect
    `NetworkPolicy`. Deployment must validate endpoints and control egress.
12. Provider composition supports only one provider kind unless a routing port is
    injected. Hot provider/profile changes do not affect accepted or future
    engine turns in the default factory.
13. `KernelStatus.context` is currently all zeros; live engine usage is emitted
    as events but not aggregated into status.
14. Diagnostics dependencies default to empty, so default local/full reports are
    mostly `skipped` and do not prove the configured provider/database/MCP is
    healthy.
15. `BlobStore`, resource and prompt ports, capabilities, and structured
    observability exist as components but are not surfaced/wired by the default
    `KairoKernel` composition.
16. The Alpha wheel now discovers only `kairo_kernel*`; the legacy source-tree
    packages remain outside the Alpha distribution and are not migrated.
17. JSON schemas document the current Python/wire shapes, but there is no built-in
    loader that constructs `KernelConfig` from `config-v1.json`.
18. SQLite uses one connection and one operation lock, so it provides correctness
    and simple transactions rather than high read/write throughput.

Frontends should not emulate missing behavior. They should feature-detect at the
composition boundary, render typed failures, refresh after replay gaps, and keep
all state changes behind optimistic revisions.
