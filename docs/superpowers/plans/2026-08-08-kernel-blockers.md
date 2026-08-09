# Kernel Blockers Fixes Implementation Plan

> **For agentic workers:** execute task-by-task with TDD; no git commits; report per-task completion.

**Goal:** Close the four kernel blockers that would corrupt TUI expectations: (1) unify public MCP invocation behind a real ToolGate (authorization, approval, timeout, fail-closed disconnect) instead of a direct `McpClient.call_tool`; (2) make global workspace moves return retryable `KERNEL_BUSY` while any turn is active and make the workspace revision advance atomically only when a write/execute tool succeeds; (3) give `KernelConfigStore` a single-process lock and an optimistic `update(expected_revision, transform)` so provider/theme/keybindings/MCP/recent-workspace writes cannot silently clobber each other; (4) complete the public exports (commands/preferences DTOs, `PreferencesPort`), align `KernelLifecyclePort.start` with the façade, and add an explicit clear-profile-override field to `PreferencesPatch` — all additive to Kernel API `"1.1"` / package `0.4.0a2`.

**Architecture:** All four blockers land inside the existing layers, preserving the `KairoKernel` façade as the only public surface. The MCP gate lives in the facade object `_Mcp` (`kairo_kernel/kernel.py`) and reuses the engine's exact authorization semantics: mode resolved from `PreferencesService`, scope classified EXTERNAL, `AuthorizationPolicy.is_authorized`, and — when policy denies — a blocking `TOOL_APPROVAL` interaction on the shared `InteractionBroker` with a synthetic (non-supervisor) turn identity, so the TUI approves through the existing `kernel.interactions.respond` and the safe default is REJECT. The workspace move gate is injected into `WorkspaceService` as an optional `active_turns` callable so both the facade and the `/workspace` command path share one check; the revision bump reuses the existing write-lease machinery via a new `WorkspaceLeaseManager.bump_revision()`. `KernelConfigStore` gains an `asyncio.Lock` and a persisted document `revision` field that `update()` verifies optimistically. B4 is pure additive surface (re-exports, one Protocol return-type correction that documents what the façade already does, one new patch field).

**Tech Stack:** Python >= 3.11, stdlib only additions (no new runtime deps; kernel stays on `aiosqlite`/`httpx`). Tests: pytest with `asyncio_mode = "auto"` (configured in `pyproject.toml`, `testpaths = ["tests/kernel"]`). Lint/type gate: `ruff check kairo_kernel tests/kernel` and `mypy kairo_kernel`, run with the project venv interpreter `.venv/Scripts/python.exe` for pytest and system-PATH `ruff`/`mypy` from `/c/Python314/Scripts`.

## Global Constraints

- **API/version:** `KERNEL_API_VERSION = "1.1"`, `EVENT_SCHEMA_VERSION = 1`, package version `0.4.0a2` — all unchanged this phase. The event envelope (schema 1) and `ChangeEvent` payload are reused as-is.
- **Additive only:** no breaking changes to existing public signatures — `plain`, WebUI and `agent/` still consume the 1.0/1.1 surface. New optional kwargs, new trailing dataclass fields **with defaults**, new private methods, and a Protocol *declaration* correction are allowed; renaming, reordering, or retyping existing public members is not. `KairoKernel.start` keeps returning `KernelResult[LifecycleState]` — the **port** is fixed to match it, never the reverse.
- **All new public API returns `KernelResult[T]` or frozen dataclasses.** Mutating facade methods keep the `_mutation_error`/`_read_error` gating pattern.
- **No new runtime dependencies.** `kairo_kernel` must stay free of `textual`, `rich`, `agent`, `fastapi`, and the legacy top-level `tools` package imports (enforced by `tests/kernel/security/test_architecture.py`), and must not contain the literal text `typing import Any` (enforced by `test_kernel_imports_are_ui_and_framework_free`).
- **TDD:** every task writes the failing test first, watches it fail, implements, watches it pass, then runs the full suite. The full suite must be green at the end of every task.
- **No git commits anywhere.** When a task is green, report completion (files changed, test counts) and stop. The user reviews and commits.
- **Line endings:** preserve each file's existing style when editing. LF: `kairo_kernel/kernel.py`, `kairo_kernel/mcp/*`, `kairo_kernel/engine/*`, `kairo_kernel/runtime/*`, `kairo_kernel/services/*` (except `factory.py`), `kairo_kernel/tools/*`, `kairo_kernel/contracts/*`, `kairo_kernel/ports/*`, `tests/kernel/*`, `docs/kernel/limitations.md`, `CHANGELOG.md`. CRLF: `kairo_kernel/__init__.py`, `kairo_kernel/factory.py`, `docs/kernel/public-api.md`, and the other `docs/kernel/*.md` files (Edit preserves CRLF automatically for pure-CRLF files; never mix endings in one file).
- **Test runner:** `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` (Windows). `ruff` and `mypy` on the global PATH at `/c/Python314/Scripts`. Baseline before Task 1: `288 passed`, ruff clean, mypy clean.

## Verified Current State (trust this; signatures quoted in tasks are real)

- **Baseline:** `288 passed in ~8s` (`.venv/Scripts/python.exe -m pytest tests/kernel -q`, re-verified). API `"1.1"` (`kairo_kernel/contracts/lifecycle.py:12`), package `0.4.0a2` (`kairo_kernel/_version.py`), event schema 1. No git commits anywhere this phase.
- **B1 MCP:** `_Mcp.call_tool/read_resource/render_prompt` (`kairo_kernel/kernel.py:818-863`) call `McpClient.call_tool/read_resource/get_prompt` directly (`kairo_kernel/mcp/client.py:60-73`) with only lifecycle gating — **no authorization, no timeout**. `McpTool.classify` returns `OperationScope.EXTERNAL` (`kairo_kernel/tools/mcp.py:35-37`); `AuthorizationPolicy.is_authorized(mode, scope)` (`kairo_kernel/tools/policy.py:22-27`) is YOLO→True, AUTO→(scope is INTERNAL), MANUAL→False. `TurnEngine._execute_tool` (`kairo_kernel/engine/turns.py:560-678`) shows the exact engine approval flow: classify → `mode = run.authorization_override or run.snapshot.options.authorization_mode` → `is_authorized` → `_request_interaction(run, InteractionKind.TOOL_APPROVAL, ..., safe_default=InteractionAction.REJECT)` with choices `APPROVE_ONCE / REJECT / STOP / ENABLE_YOLO|ENABLE_AUTO` → REJECT on non-approval → `run.authorization_override` + `_apply_authorization` on ENABLE_*. `InteractionBroker.request(request, cancellation)` (`kairo_kernel/runtime/interactions.py:39`) returns `InteractionResponse` on resolve, else `safe_default` (forced to REJECT) on cancel/expiry/shutdown; `respond()` validates action, ids, expiry; `pending()` returns pending requests; the broker correlates purely by `interaction_id` + `turn_id`. `StdioTransport.request` blocks on `readline` with no `wait_for` (`kairo_kernel/mcp/transport.py:45-55`); `McpTool.execute` has no timeout (`kairo_kernel/tools/mcp.py:39-72`); builtins wrap with `asyncio.wait` at `kairo_kernel/tools/base.py:103-138`. `AuthorizationGate` (`kairo_kernel/tools/registry.py:16-61`) is dead code, not used. Facade tests: `tests/kernel/mcp/test_facade.py` (MemoryTransport fake, `McpServerTrustStore.trust`, `McpHub((client,))`).
- **B2 workspace:** `_Workspace.move` (`kernel.py:602-611`) has no active-turn check. `WorkspaceService.move` (`kairo_kernel/services/workspaces.py:294-337`) checks degraded → writes lease → revision conflict → resolve/validate → apply participants → `leases.update`. `SessionTurnSupervisor.active()` (`kairo_kernel/runtime/turns.py:68-70`) returns `tuple[tuple[SessionId, TurnId], ...]` but is not exposed to `WorkspaceService`. Accept-time capture: `_resolve_options` overlays `workspace_leases.snapshot()` into `EngineOptions` (`engine/turns.py:157-159`) frozen into `RunSnapshot` (`:199`); tools re-resolve live leases per call (`kairo_kernel/tools/files.py:51-52` `_lease()`, used at `:79,122,157,238,316`). `WorkspaceLeaseManager.snapshot()` and `read()` wait while a writer holds the lease, so a gate check **inside the write lease** makes accept-time capture consistent. `WORKSPACE_CHANGED` after write/exec tools (`turns.py:667-677`): condition `result.status is SUCCEEDED and workspace_leases is not None and _mutates_workspace(run.snapshot.tools, call.name)`, emits `snapshot().revision` but nothing increments it — only `WorkspaceLeaseManager.update` (`runtime/workspace.py:70-75`) increments, called from move and bookmark mutations. Command path: `CommandService._move_workspace` (`services/commands.py:351-368`) calls `service.move` directly — a gate inside `WorkspaceService` covers both routes. `tests/kernel/engine/test_workspace_change_events.py` seeds `WorkspaceLeaseManager("C:/ws", revision=3)` and asserts the event revision is `3`.
- **B3 config store:** `KernelConfigStore` (`kairo_kernel/services/config_document.py:45-113`): `load`/`save`/atomic `_write_sync` (mkstemp + fsync + `os.replace`), **no lock, no revision**. `KernelConfigDocument` (`:31-42`) has no revision field; `document_to_json/from_json` (`:143-197`). `DocumentProviderCatalog.save` (`:131-140`) is read-modify-write, last-write-wins. Today only `DocumentProviderCatalog.save` writes (via `ProviderService._save` under its per-instance lock); theme/keybindings/recent_workspaces are never written yet. `ProviderCatalogSnapshot.revision` is in-memory only (`services/providers.py:30-33`; `DocumentProviderCatalog.load` returns `ProviderCatalogSnapshot(0, ...)` at `:129`). Existing tests: `tests/kernel/services/test_config_document.py` (6 tests).
- **B4 exports/port/patch:** `kairo_kernel/__init__.py:10-21` exports `KairoKernel, __version__, KernelConfig, KernelDependencies, KernelError, KernelResult, KERNEL_API_VERSION, build_kernel, contracts, ports`. `contracts/__init__.py:5-41` re-exports content/enums/events/identifiers/interactions/json/lifecycle/providers/support/tools/turns but **not** `commands` or `preferences`. `ports/__init__.py:3-29` does **not** export `PreferencesPort` (`kairo_kernel/ports/preferences.py:10`). `KernelLifecyclePort.start` (`kairo_kernel/ports/control.py:46`) declares `KernelResult[KernelStatus]`; the façade returns `KernelResult[LifecycleState]` (`kernel.py:174-192`) — the port is wrong. `capabilities()` matches (`KernelCapabilities`); `status()` matches; `shutdown(request: ShutdownRequest)` vs façade `shutdown(request: ShutdownRequest | None = None)` is compatible (optional param satisfies the Protocol). `PreferencesPatch` (`contracts/preferences.py:35-43`) has no clear-profile field; `PreferencesService.patch` (`services/preferences.py:24-58`) sets `profile_id=current.profile_id if patch.profile_id is None else patch.profile_id`. Existing public-import test: `tests/kernel/contracts/test_contracts.py:345-349`; `_specimens()` at `:149-261` parametrizes `test_every_contract_round_trips` (`:264-266`).

## Task Index

| # | Task | New tests | Suite total after |
|---|------|-----------|-------------------|
| 1 | Config document `revision` + `KernelConfigStore.update()` + catalog delegation | +4 | 292 |
| 2 | Contract exports: commands/preferences DTOs + round-trip specimens | +6 | 298 |
| 3 | Port exports (`PreferencesPort`) + `KernelLifecyclePort.start` alignment + public-import assertions | +0 | 298 |
| 4 | `PreferencesPatch.clear_profile_id` + service honor + validation | +2 | 300 |
| 5 | Workspace move active-turn gate (service injection + wiring) | +1 | 301 |
| 6 | Workspace move/turn race integration tests (facade + `/workspace` command) | +2 | 303 |
| 7 | Workspace revision bump on write/execute tool success | +1 | 304 |
| 8 | MCP facade ToolGate (authorization + approval + timeout + fail-closed disconnect) | +7 | 311 |
| 9 | Docs (limitations/changelog/public-api) + full gate | +0 | 311 |

---

## Task 1: Config document `revision` + `KernelConfigStore.update(expected_revision, transform)`

**Purpose:** Give the versioned global config document a persisted revision and a single-process, optimistically-locked mutation entry point, and route `DocumentProviderCatalog` through it. This is the foundation every later config write (theme/keybindings/MCP/recent-workspaces, TUI phase) will use.

**Files:**
- Modify: `kairo_kernel/services/config_document.py` (`KernelConfigDocument` :31-42, `KernelConfigStore` :45-113, `DocumentProviderCatalog.save` :131-140, `document_to_json` :143-153, `document_from_json` :156-197)
- Modify: `tests/kernel/services/test_config_document.py`

**Interfaces:**
- Consumes: existing `KernelConfigDocument`, `KernelConfigStore.load/save`, `_failure(code, message, operation, *, retryable=False)`, `replace`, `ErrorCode.CONFLICT`, `ErrorCode.NOT_FOUND`.
- Produces:
  - `KernelConfigDocument.revision: int = 0` (new **trailing** field; positional callers with 8 args keep working).
  - `KernelConfigStore.update(expected_revision: int, transform: DocumentTransform) -> KernelResult[int]` where `DocumentTransform = Callable[[KernelConfigDocument], KernelConfigDocument]` (new, public entry for all document writes).
  - `DocumentProviderCatalog.save` now delegates through `store.update` (behavior preserved for single writers, CONFLICT for concurrent ones).
  - JSON gains `"revision"`; `document_from_json` defaults missing revision to 0 (old documents keep loading).

**Steps**

- [ ] **Step 1: Write the failing tests** (append to `tests/kernel/services/test_config_document.py`; add `from dataclasses import replace` to its imports)

```python
def test_store_update_first_write_advances_document_revision(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        updated = await store.update(0, lambda document: replace(document, theme="dark"))
        assert updated.ok and updated.value == 1
        loaded = (await store.load()).value
        assert loaded is not None
        assert loaded.revision == 1 and loaded.theme == "dark"
        assert document_to_json(loaded)["revision"] == 1

    asyncio.run(exercise())


def test_store_update_stale_expected_revision_is_conflict(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        assert (await store.update(0, lambda document: document)).ok
        conflict = await store.update(0, lambda document: document)
        assert conflict.error is not None and conflict.error.code is ErrorCode.CONFLICT
        loaded = (await store.load()).value
        assert loaded is not None and loaded.revision == 1

    asyncio.run(exercise())


def test_store_update_concurrent_writers_one_wins_one_conflicts(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        first = asyncio.create_task(store.update(0, lambda document: replace(document, theme="one")))
        second = asyncio.create_task(store.update(0, lambda document: replace(document, theme="two")))
        results = await asyncio.gather(first, second)
        codes = sorted(result.error.code.value if result.error is not None else "ok" for result in results)
        assert codes == ["conflict", "ok"]
        loaded = (await store.load()).value
        assert loaded is not None and loaded.revision == 1
        assert loaded.theme in ("one", "two")

    asyncio.run(exercise())


def test_store_update_rejects_invalid_transforms(tmp_path: Path) -> None:
    def broken(_document: KernelConfigDocument) -> KernelConfigDocument:
        raise ValueError("transform exploded")

    def wrong_type(_document: KernelConfigDocument) -> KernelConfigDocument:
        return "not a document"  # type: ignore[return-value]

    async def exercise() -> None:
        store = KernelConfigStore(tmp_path / "config-v1.json")
        exploded = await store.update(0, broken)
        assert exploded.error is not None and exploded.error.code is ErrorCode.CONFIG_INVALID
        mistyped = await store.update(0, wrong_type)
        assert mistyped.error is not None and mistyped.error.code is ErrorCode.CONFIG_INVALID
        assert (await store.load()).error is not None  # nothing was persisted

    asyncio.run(exercise())
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_config_document.py -q`
Expected: FAIL — `AttributeError: 'KernelConfigDocument' object has no attribute 'revision'` / `TypeError: KernelConfigStore.update() missing` (update does not exist).

- [ ] **Step 3: Implement**

Edit `kairo_kernel/services/config_document.py`:

1. Imports — keep `from dataclasses import dataclass, replace` and add `Callable` from `collections.abc` to the import block:

```python
from collections.abc import Callable
```

2. Add the transform alias right after `ResultT = TypeVar("ResultT")` (`:28`):

```python
DocumentTransform = Callable[[KernelConfigDocument], KernelConfigDocument]
```

3. Add the trailing `revision` field to `KernelConfigDocument` (`:31-42`) — after `recent_workspaces`:

```python
@dataclass(frozen=True)
class KernelConfigDocument:
    """Global user-level configuration; secret values never appear here."""

    version: int = CONFIG_DOCUMENT_VERSION
    profiles: tuple[ProviderProfile, ...] = ()
    roles: tuple[ProviderRoleMapping, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    default_profile_id: ProfileId | None = None
    theme: str = "default"
    keybindings: tuple[tuple[str, str], ...] = ()
    recent_workspaces: tuple[str, ...] = ()
    revision: int = 0
```

4. `KernelConfigStore` — add the lock to `__init__` and add `update` after `save`:

```python
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
```

```python
    async def update(
        self,
        expected_revision: int,
        transform: DocumentTransform,
    ) -> KernelResult[int]:
        """Optimistically mutate the document under a single-process lock.

        The transform receives the current document and returns the updated
        document; the store persists it with ``revision = expected_revision + 1``.
        A stale ``expected_revision`` (or a concurrent writer that already
        advanced the document) fails with ``ErrorCode.CONFLICT``.
        """
        async with self._lock:
            loaded = await self.load()
            if loaded.error is not None and loaded.error.code is not ErrorCode.NOT_FOUND:
                return KernelResult.failure(loaded.error)
            document = loaded.value if loaded.value is not None else KernelConfigDocument()
            if document.revision != expected_revision:
                return _failure(
                    ErrorCode.CONFLICT,
                    "Configuration revision has changed.",
                    "config.document.update",
                )
            try:
                updated = transform(document)
            except ValueError as exc:
                return _failure(
                    ErrorCode.CONFIG_INVALID,
                    f"Configuration transform failed: {exc}",
                    "config.document.update",
                )
            if not isinstance(updated, KernelConfigDocument):
                return _failure(
                    ErrorCode.CONFIG_INVALID,
                    "Configuration transform must return a KernelConfigDocument.",
                    "config.document.update",
                )
            committed = replace(updated, revision=expected_revision + 1)
            saved = await self.save(committed)
            if saved.error is not None:
                return KernelResult.failure(saved.error)
            return KernelResult.success(committed.revision)
```

5. `DocumentProviderCatalog.save` — delegate through `update` (replace the whole method `:131-140`):

```python
    async def save(self, snapshot: ProviderCatalogSnapshot) -> KernelResult[ProviderCatalogSnapshot]:
        loaded = await self._store.load()
        if loaded.error is not None and loaded.error.code is not ErrorCode.NOT_FOUND:
            return KernelResult.failure(loaded.error)
        expected = loaded.value.revision if loaded.value is not None else 0
        updated = await self._store.update(
            expected,
            lambda document: replace(document, profiles=snapshot.profiles, roles=snapshot.roles),
        )
        if updated.error is not None:
            return KernelResult.failure(updated.error)
        return KernelResult.success(snapshot)
```

6. `document_to_json` — add the revision key right after `"version"`; the full function becomes (`:143-153`):

```python
def document_to_json(document: KernelConfigDocument) -> dict[str, object]:
    return {
        "version": document.version,
        "revision": document.revision,
        "default_profile_id": str(document.default_profile_id) if document.default_profile_id is not None else None,
        "theme": document.theme,
        "profiles": [thaw_json(profile.to_json_value()) for profile in document.profiles],
        "roles": [{"role": mapping.role, "profile_id": str(mapping.profile_id)} for mapping in document.roles],
        "mcp_servers": [_server_to_json(server) for server in document.mcp_servers],
        "keybindings": [[key, command] for key, command in document.keybindings],
        "recent_workspaces": list(document.recent_workspaces),
    }
```

7. `document_from_json` — parse and validate the revision, then pass it as the trailing argument. Add a helper after `_optional_str`:

```python
def _int(payload: dict[str, object], key: str, default: int = 0) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Configuration field '{key}' must be an integer.")
    return value
```

In `document_from_json`, right after the version checks (`:159-163`) add:

```python
    revision = _int(payload, "revision", 0)
    if revision < 0:
        raise ValueError("Configuration document revision cannot be negative.")
```

and change the final constructor call (`:188-197`) from the 8-arg positional form to append `revision`:

```python
    return KernelConfigDocument(
        version,
        tuple(profiles),
        tuple(roles),
        tuple(_server_from_json(item) for item in _list(payload, "mcp_servers")),
        ProfileId(default_profile) if default_profile is not None else None,
        _str(payload, "theme", "default"),
        tuple(keybindings),
        tuple(_str_list(payload, "recent_workspaces")),
        revision,
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_config_document.py -q`
Expected: `10 passed` (6 existing + 4 new).

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `292 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 2: Contract exports — commands/preferences DTOs + round-trip specimens

**Purpose:** Make `kairo_kernel.contracts.commands` and `kairo_kernel.contracts.preferences` first-class public modules (importable and re-exported), and extend the contract round-trip test with their DTO specimens.

**Files:**
- Modify: `kairo_kernel/contracts/__init__.py`
- Modify: `tests/kernel/contracts/test_contracts.py` (imports, `_specimens()`, `test_public_import_smoke`)

**Interfaces:**
- Consumes: existing DTOs — `CommandArgument, KernelCommand, ParsedCommand, CommandOutcome` (`kairo_kernel/contracts/commands.py`), `PreferencesSnapshot, PreferencesPatch` (`kairo_kernel/contracts/preferences.py`).
- Produces: `kairo_kernel.contracts.commands` / `kairo_kernel.contracts.preferences` accessible as attributes of the `kairo_kernel.contracts` package (a `from ... import` inside `__init__.py` binds the submodule attribute automatically), and the DTO names re-exported at `kairo_kernel.contracts.*`.

**Steps**

- [ ] **Step 1: Write the failing test** — extend `test_public_import_smoke` in `tests/kernel/contracts/test_contracts.py` (`:345-349`):

```python
def test_public_import_smoke() -> None:
    import kairo_kernel

    assert kairo_kernel.contracts.KERNEL_API_VERSION == "1.1"
    assert inspect.isclass(kairo_kernel.KernelError)
    assert inspect.isclass(kairo_kernel.contracts.commands.KernelCommand)
    assert inspect.isclass(kairo_kernel.contracts.commands.CommandOutcome)
    assert inspect.isclass(kairo_kernel.contracts.preferences.PreferencesPatch)
    assert inspect.isclass(kairo_kernel.contracts.preferences.PreferencesSnapshot)
```

(The `kairo_kernel.ports.preferences.PreferencesPort` assertions are added in Task 3, which is where the port exports land.)

Also add the round-trip specimens: extend the imports at the top of the file (insert before the `content` import block, keeping alphabetical order) and extend `_specimens()`:

```python
from kairo_kernel.contracts.commands import CommandArgument, CommandOutcome, KernelCommand, ParsedCommand
```

```python
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
```

In `_specimens()`, after the `errors` tuple, add:

```python
    commands = (
        CommandArgument("path", required=True, greedy=True),
        KernelCommand(
            "/run",
            "Run a task",
            "Run a task with one argument",
            (CommandArgument("path", required=True),),
            mutates=True,
            needs_session=True,
        ),
        ParsedCommand("/run", ("task",)),
        CommandOutcome("/run", "done", SessionId("session-1")),
    )
    preferences = (
        PreferencesSnapshot(0, authorization_mode=AuthorizationMode.AUTO, plan_mode=True, profile_id=ProfileId("p/m")),
        PreferencesPatch(0, plan_mode=True),
    )
```

and change the return line (`:261`) to append them:

```python
    return (
        content
        + provider
        + tools
        + lifecycle
        + turns
        + interactions
        + support
        + event_payloads
        + events
        + (EventReplay(events, 1, len(events)),)
        + errors
        + commands
        + preferences
    )
```

(Note: `PreferencesPatch(0, plan_mode=True)` round-trips already in this task — `clear_profile_id` is absent, so it defaults to `False` and serializes as such. Task 4 extends this specimen to `PreferencesPatch(0, plan_mode=True, clear_profile_id=True)` to cover the new field; the specimen count stays unchanged.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/contracts/test_contracts.py -q`
Expected: FAIL — `ModuleNotFoundError: kairo_kernel.contracts.commands` (no such attribute yet) and the round-trip parametrization does not yet include the new specimens.

- [ ] **Step 3: Implement** — edit `kairo_kernel/contracts/__init__.py`: add the two imports in alphabetical position (commands before content; preferences after providers):

```python
from kairo_kernel.contracts.commands import (
    CommandArgument,
    CommandOutcome,
    KernelCommand,
    ParsedCommand,
)
```

```python
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
```

The `__all__` is computed dynamically (`[name for name in globals() if not name.startswith("_")]`), so the new names are exported automatically; the submodule attributes `kairo_kernel.contracts.commands` / `.preferences` are bound by the imports.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/contracts/test_contracts.py -q`
Expected: PASS — public-import assertions hold; the round-trip parametrization now runs 6 extra cases (all green).

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `298 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 3: Port exports (`PreferencesPort`) + `KernelLifecyclePort.start` alignment + public-import assertions

**Purpose:** Complete the public port surface: export `PreferencesPort` at `kairo_kernel.ports.*`, expose `kairo_kernel.ports.preferences` as a module, and correct the `KernelLifecyclePort.start` declaration so it documents what the façade actually returns (the façade is unchanged).

**Files:**
- Modify: `kairo_kernel/ports/__init__.py`
- Modify: `kairo_kernel/ports/control.py`
- Modify: `tests/kernel/contracts/test_contracts.py` (add the ports assertion to `test_public_import_smoke`)

**Interfaces:**
- Consumes: `PreferencesPort` (`kairo_kernel/ports/preferences.py:10`, Protocol with `snapshot()` and `apply_authorization(mode)`), `LifecycleState` (`kairo_kernel/contracts/enums.py`).
- Produces: `kairo_kernel.ports.PreferencesPort` re-export and `kairo_kernel.ports.preferences` module attribute; `KernelLifecyclePort.start() -> KernelResult[LifecycleState]` (declaration now matches `KairoKernel.start`).

**Steps**

- [ ] **Step 1: Write the failing test** — add the ports assertion to `test_public_import_smoke` (`tests/kernel/contracts/test_contracts.py`), the line that was deferred in Task 2:

```python
    assert inspect.isclass(kairo_kernel.ports.preferences.PreferencesPort)
    assert kairo_kernel.ports.PreferencesPort is kairo_kernel.ports.preferences.PreferencesPort
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/contracts/test_contracts.py::test_public_import_smoke -q`
Expected: FAIL — `AttributeError: module 'kairo_kernel.ports' has no attribute 'PreferencesPort'`.

- [ ] **Step 3: Implement**

1. `kairo_kernel/ports/__init__.py` — add the import after `from kairo_kernel.ports.interactions import InteractionPort` and add the name to `__all__` (alphabetically after `"ObservabilityPort"`):

```python
from kairo_kernel.ports.preferences import PreferencesPort
```

```python
    "ObservabilityPort",
    "PreferencesPort",
    "PromptPort",
```

2. `kairo_kernel/ports/control.py` — import `LifecycleState` and correct the `start` declaration (the other members already match the façade; do not touch `shutdown` or `capabilities`):

```python
from kairo_kernel.contracts.lifecycle import KernelCapabilities, LifecycleState, KernelStatus, ShutdownReport, ShutdownRequest
```

```python
class KernelLifecyclePort(Protocol):
    async def start(self) -> KernelResult[LifecycleState]: ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/contracts/test_contracts.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `298 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 4: `PreferencesPatch.clear_profile_id` + service honor + validation

**Purpose:** Let a patch explicitly clear the runtime profile override instead of requiring the caller to read the snapshot and pass `profile_id=None` (which today is indistinguishable from "leave unchanged").

**Files:**
- Modify: `kairo_kernel/contracts/preferences.py`
- Modify: `kairo_kernel/services/preferences.py`
- Modify: `tests/kernel/services/test_preferences.py`

**Interfaces:**
- Consumes: `PreferencesPatch` (frozen Contract, `expected_revision` first field), `PreferencesService.patch`.
- Produces: `PreferencesPatch.clear_profile_id: bool = False` (new **trailing** field), `PreferencesPatch.__post_init__` raising `ValueError` when both `clear_profile_id` and `profile_id` are set; `patch()` maps `clear_profile_id=True` to `profile_id=None` in the next snapshot.

**Steps**

- [ ] **Step 1: Write the failing tests** (append to `tests/kernel/services/test_preferences.py`)

```python
def test_patch_clear_profile_id_resets_profile() -> None:
    async def exercise() -> None:
        service = PreferencesService(PreferencesSnapshot(0, profile_id=ProfileId("p/m")))
        patched = await service.patch(PreferencesPatch(0, clear_profile_id=True))
        assert patched.ok and patched.value is not None
        assert patched.value.profile_id is None
        assert patched.value.revision == 1
        assert patched.value.authorization_mode is AuthorizationMode.MANUAL  # unchanged

    asyncio.run(exercise())


def test_patch_clear_and_profile_together_are_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        PreferencesPatch(0, profile_id=ProfileId("p/m"), clear_profile_id=True)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_preferences.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'clear_profile_id'` (field does not exist; the both-set guard has nothing to raise).

- [ ] **Step 3: Implement**

1. `kairo_kernel/contracts/preferences.py` — add the trailing field and the guard to `PreferencesPatch` (`:35-43`):

```python
@dataclass(frozen=True)
class PreferencesPatch(Contract):
    expected_revision: int
    authorization_mode: AuthorizationMode | None = None
    plan_mode: bool | None = None
    thinking_mode: bool | None = None
    context_trigger_percent: float | None = None
    context_target_percent: float | None = None
    preserve_recent_turns: int | None = None
    profile_id: ProfileId | None = None
    clear_profile_id: bool = False

    def __post_init__(self) -> None:
        if self.clear_profile_id and self.profile_id is not None:
            raise ValueError("clear_profile_id and profile_id cannot both be set.")
```

2. `kairo_kernel/services/preferences.py` — honor the flag in `patch` (`:53`):

```python
                    profile_id=(
                        None
                        if patch.clear_profile_id
                        else (current.profile_id if patch.profile_id is None else patch.profile_id)
                    ),
```

(Note: the `ValueError` from `__post_init__` fires at construction — contracts stay valid-by-construction, exactly like `PreferencesSnapshot.__post_init__` (`test_preferences.py::test_snapshot_contract_rejects_invalid_construction` asserts `ValueError`). Patch-time validation errors (bad ranges) keep flowing through `patch`'s existing `except ValueError` → `ErrorCode.CONFIG_INVALID` mapping, which the existing `test_patch_conflict_and_invalid_ranges_are_typed` covers. Also extend the Task 2 round-trip specimen: in `tests/kernel/contracts/test_contracts.py` change the preferences specimen to `PreferencesPatch(0, plan_mode=True, clear_profile_id=True)` so the new field is covered by `test_every_contract_round_trips` — the specimen count does not change.)

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_preferences.py -q`
Expected: `6 passed` (4 existing + 2 new).

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `300 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 5: Workspace move active-turn gate (service injection + wiring)

**Purpose:** Make every workspace **move** (facade and `/workspace` command alike) fail closed with retryable `KERNEL_BUSY` while any turn is active, so the accept-time workspace captured by a turn can never be invalidated mid-turn. The gate lives in `WorkspaceService` via an optional injected `active_turns` callable so both routes share it.

**Files:**
- Modify: `kairo_kernel/services/workspaces.py` (imports, `ActiveTurns` alias, `WorkspaceService.__init__`, `move`, `_active_turn_check`, `_failure`)
- Modify: `kairo_kernel/factory.py` (wire `active_turns=supervisor.active`)
- Modify: `tests/kernel/services/test_workspace_service.py` (`service_for` helper + new test)

**Interfaces:**
- Consumes: `SessionTurnSupervisor.active() -> tuple[tuple[SessionId, TurnId], ...]` (`kairo_kernel/runtime/turns.py:68-70`), `ErrorCode.KERNEL_BUSY`.
- Produces:
  - `ActiveTurns = Callable[[], Awaitable[tuple[tuple[SessionId, TurnId], ...]]]` (new alias).
  - `WorkspaceService.__init__(..., active_turns: ActiveTurns | None = None)` (new **optional keyword**; existing constructions unaffected).
  - `move` returns `KernelResult[WorkspaceState]` with `KernelError(code=KERNEL_BUSY, retryable=True, operation="workspace.move")` while the injected callable reports active turns; the check runs **inside the write lease**, so a concurrent turn admission either happens before the check (→ BUSY) or blocks on the write lease until the move commits and then snapshots the new root (consistent).
  - `_failure(code, message, operation, cause=None, *, retryable=False)` (private helper extended with a keyword-only flag).

**Steps**

- [ ] **Step 1: Write the failing test** — extend the `service_for` helper and add a test in `tests/kernel/services/test_workspace_service.py`. First update the helper (`:76-94`):

```python
def service_for(
    root: Path,
    *,
    bookmarks: InMemoryWorkspaceBookmarks | None = None,
    participants: tuple[WorkspaceParticipant, ...] = (),
    degraded: FakeDegraded | None = None,
    preview_limit: int = 8,
    active_turns: object | None = None,
) -> tuple[WorkspaceService, FakeWorkspaceRepository]:
    repository = FakeWorkspaceRepository(root)
    service = WorkspaceService(
        repository,
        WorkspaceLeaseManager(str(root.resolve())),
        bookmarks=bookmarks,
        participants=participants,
        degraded=degraded,
        active_turns=active_turns,
        preview_limit_bytes=preview_limit,
        preview_child_limit=2,
    )
    return service, repository
```

Then add the imports and the test. Add to the top-of-file imports:

```python
from kairo_kernel.contracts.identifiers import SessionId, TurnId
```

Add the test (after `test_move_uses_bookmark_and_turn_snapshot_isolation`):

```python
async def test_move_returns_busy_while_turns_active(tmp_path: Path) -> None:
    target = tmp_path / "next"
    target.mkdir()
    active: tuple[tuple[SessionId, TurnId], ...] = ()

    async def fake_active_turns() -> tuple[tuple[SessionId, TurnId], ...]:
        return active

    service, _ = service_for(tmp_path, active_turns=fake_active_turns)
    active = ((SessionId("session-1"), TurnId("turn-1")),)
    busy = await service.move(str(target), 0)
    assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY
    assert busy.error.retryable
    assert (await service.snapshot()).root == str(tmp_path.resolve())  # unchanged

    active = ()
    moved = await service.move(str(target), 0)
    assert moved.ok and moved.value is not None
    assert moved.value.root == str(target.resolve())
```

(Note: this test is written as an `async def test_...` at module level, matching the existing style in this file.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_workspace_service.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'active_turns'`.

- [ ] **Step 3: Implement**

1. `kairo_kernel/services/workspaces.py` — imports: add `Awaitable, Callable` to the existing `from collections.abc import AsyncIterator`, and add a new identifier import:

```python
from collections.abc import AsyncIterator, Awaitable, Callable
```

```python
from kairo_kernel.contracts.identifiers import SessionId, TurnId
```

2. Add the alias after `BookmarkMutation` (`:101-103`):

```python
ActiveTurns = Callable[[], Awaitable[tuple[tuple[SessionId, TurnId], ...]]]
```

3. Extend `WorkspaceService.__init__` (`:119-138`) — add the parameter after `degraded` and store it:

```python
    def __init__(
        self,
        repository: WorkspaceRepositoryPort,
        leases: WorkspaceLeaseManager,
        *,
        bookmarks: WorkspaceBookmarkRepository | None = None,
        participants: tuple[WorkspaceParticipant, ...] = (),
        degraded: DegradedSignal | None = None,
        active_turns: ActiveTurns | None = None,
        preview_limit_bytes: int = 256 * 1024,
        preview_child_limit: int = 500,
    ) -> None:
        self._repository = repository
        self._leases = leases
        self._bookmarks = bookmarks or InMemoryWorkspaceBookmarks()
        self._participants = participants
        self._degraded = degraded
        self._active_turns = active_turns
        self._preview_limit_bytes = max(1, preview_limit_bytes)
        self._preview_child_limit = max(1, preview_child_limit)
        self._degraded_reason = ""
        self._mutation_lock = asyncio.Lock()
```

4. In `move` (`:294-300`), insert the gate right after the write lease is acquired (inside `async with lease:`, before the revision-conflict check):

```python
    async def move(self, target: str, expected_revision: int) -> KernelResult[WorkspaceState]:
        if self._degraded_reason:
            return _failure(ErrorCode.KERNEL_DEGRADED, "Workspace mutations are disabled.", "workspace.move")
        clean = target.strip()
        if not clean:
            return _failure(ErrorCode.INVALID_ARGUMENT, "Workspace target is required.", "workspace.move")
        async with self._mutation_lock:
            lease = await self._leases.write()
            async with lease:
                busy = await self._active_turn_check()
                if busy is not None:
                    return busy
                conflict = self._revision_conflict(lease, expected_revision, "workspace.move")
```

5. Add the private check method (place it next to `_revision_conflict`):

```python
    async def _active_turn_check(self) -> KernelResult[WorkspaceState] | None:
        if self._active_turns is None:
            return None
        active = await self._active_turns()
        if active:
            return _failure(
                ErrorCode.KERNEL_BUSY,
                "Cannot move the workspace while a turn is active.",
                "workspace.move",
                retryable=True,
            )
        return None
```

6. Extend the `_failure` helper (`:476-483`) with the keyword-only flag:

```python
def _failure(
    code: ErrorCode,
    message: str,
    operation: str,
    cause: BaseException | None = None,
    *,
    retryable: bool = False,
) -> KernelResult[ResultT]:
    del cause
    return KernelResult.failure(KernelError(code, message, retryable, operation))
```

7. `kairo_kernel/factory.py` — wire the real supervisor (CRLF file; Edit preserves CRLF). Change `:206`:

```python
    workspace_service = WorkspaceService(workspace_repository, leases, active_turns=supervisor.active)
```

(Note: bookmark mutations `save_bookmark`/`remove_bookmark` bump the revision but never change the root, so they are intentionally **not** gated — a turn's accept-time root stays valid. Only `move` changes the root.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/services/test_workspace_service.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `301 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 6: Workspace move/turn race integration tests (facade + `/workspace` command)

**Purpose:** Prove the gate end-to-end with a real engine turn in flight: the facade `kernel.workspace.move` and the `/workspace` command both return retryable `KERNEL_BUSY` while a turn is RUNNING, and both succeed after the turn ends.

**Files:**
- Create: `tests/kernel/test_workspace_move_gate.py`

**Interfaces:**
- Consumes: `build_kernel`/`KernelConfig`/`KernelDependencies` (`kairo_kernel/factory.py`), `FakeProvider.block` (blocks in `stream` until cancelled; `tests/kernel/engine/fakes.py:93-100`), `TurnRequest`, `TurnStatus`, `ErrorCode`.
- Produces: two integration tests proving the Task 5 gate end-to-end (no new production code).

**Steps**

- [ ] **Step 1: Write the failing tests** — create `tests/kernel/test_workspace_move_gate.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.contracts.enums import ErrorCode, ProviderStreamKind, TurnStatus
from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.providers import ProviderStreamEvent
from kairo_kernel.contracts.turns import TurnRequest
from tests.kernel.engine.fakes import FakeProvider, FakeSessions, session


async def _wait_running(kernel, turn_id) -> None:
    for _ in range(200):
        snapshot = await kernel.turn(turn_id)
        if snapshot.value is not None and snapshot.value.status is TurnStatus.RUNNING:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("turn did not reach RUNNING")


def test_workspace_move_returns_busy_while_turn_active_then_succeeds(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = str(tmp_path)
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        provider.block = True
        kernel = build_kernel(
            KernelConfig(
                root,
                database_path=str(root + "/kernel.db"),
                default_session_id=SessionId("session-1"),
                enable_builtin_tools=False,
            ),
            KernelDependencies(provider=provider, sessions=FakeSessions(session())),
        )
        target = tmp_path / "next"
        target.mkdir()
        async with kernel:
            accepted = await kernel.submit(TurnRequest("work", SessionId("session-1")))
            assert accepted.value is not None
            await _wait_running(kernel, accepted.value.turn_id)

            state = await kernel.workspace.snapshot()
            busy = await kernel.workspace.move(str(target), state.revision)
            assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY
            assert busy.error.retryable
            after = await kernel.workspace.snapshot()
            assert after.root == state.root and after.revision == state.revision  # untouched

            assert (await kernel.cancel(accepted.value.turn_id)).ok
            assert (await kernel.wait(accepted.value.turn_id, 2)).ok
            moved = await kernel.workspace.move(str(target), state.revision)
            assert moved.ok and moved.value is not None
            assert moved.value.root == str(target.resolve())

    asyncio.run(exercise())


def test_workspace_command_returns_busy_while_turn_active(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = str(tmp_path)
        provider = FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),))
        provider.block = True
        kernel = build_kernel(
            KernelConfig(
                root,
                database_path=str(root + "/kernel.db"),
                default_session_id=SessionId("session-1"),
                enable_builtin_tools=False,
            ),
            KernelDependencies(provider=provider, sessions=FakeSessions(session())),
        )
        target = tmp_path / "next"
        target.mkdir()
        async with kernel:
            accepted = await kernel.submit(TurnRequest("work", SessionId("session-1")))
            assert accepted.value is not None
            await _wait_running(kernel, accepted.value.turn_id)

            parsed = kernel.commands.parse(f"/workspace {target}")
            assert parsed.ok and parsed.value is not None
            busy = await kernel.commands.execute(parsed.value, SessionId("session-1"))
            assert busy.error is not None and busy.error.code is ErrorCode.KERNEL_BUSY

            assert (await kernel.cancel(accepted.value.turn_id)).ok
            assert (await kernel.wait(accepted.value.turn_id, 2)).ok
            retried = await kernel.commands.execute(
                kernel.commands.parse(f"/workspace {target}").value, SessionId("session-1")
            )
            assert retried.ok and retried.value is not None

    asyncio.run(exercise())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/test_workspace_move_gate.py -q`
Expected: FAIL — the move currently succeeds mid-turn (the `busy.error is not None` assertion fails; the move commits and the snapshot assertions fail).

- [ ] **Step 3: Implement** — no production code in this task: Task 5's gate is the implementation. Verify the factory wiring from Task 5 is present (`grep -n "active_turns=supervisor.active" kairo_kernel/factory.py`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/test_workspace_move_gate.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `303 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---
## Task 7: Workspace revision bump on write/execute tool success

**Purpose:** Make the workspace revision grow only when a write/execute tool actually succeeds: the engine atomically bumps the revision **then** publishes `WORKSPACE_CHANGED` carrying the **new** revision; a failed tool bumps nothing and emits nothing.

**Files:**
- Modify: `kairo_kernel/runtime/workspace.py` (`WorkspaceLeaseManager` — add `bump_revision`)
- Modify: `kairo_kernel/engine/turns.py` (`_execute_tool` success path, `:667-677`)
- Modify: `tests/kernel/engine/test_workspace_change_events.py`

**Interfaces:**
- Consumes: `WorkspaceLeaseManager.write()/update(lease, root)` (`kairo_kernel/runtime/workspace.py:56-75`), `_mutates_workspace(run.snapshot.tools, call.name)` (`engine/turns.py:887-891`), `ChangeEvent(revision, subject_id, summary)`.
- Produces: `WorkspaceLeaseManager.bump_revision() -> WorkspaceSnapshot` (new) — acquires a write lease and calls `update(lease, lease.snapshot.root)`, so the increment is atomic under the manager's lock and all revision mutations keep flowing through the write-lease invariant. The engine success path now emits `ChangeEvent(workspace.revision, call.name, "Workspace files changed.")` where `workspace` is the **post-bump** snapshot.

**Steps**

- [ ] **Step 1: Update the existing test and write the failing one** — `tests/kernel/engine/test_workspace_change_events.py`:

1. In `test_write_permission_tool_emits_workspace_changed` (`:60-74`) change the seeded-revision assertion and add a growth assertion:

```python
def test_write_permission_tool_emits_workspace_changed() -> None:
    async def exercise() -> None:
        provider = FakeProvider(*_script("write_file"))
        leases = WorkspaceLeaseManager("C:/ws", revision=3)
        engine, bus = _engine(provider, FakeTools(WriteTool("write_file")), leases)
        accepted = await engine.submit(TurnRequest("go", SessionId("session-1")))
        assert accepted.value is not None
        result = await engine.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.SUCCEEDED
        events = [event for event in (await bus.snapshot()).events if event.event_type is EventType.WORKSPACE_CHANGED]
        assert len(events) == 1
        assert isinstance(events[0].payload, ChangeEvent)
        assert events[0].payload.revision == 4  # seeded 3, bumped on tool success
        assert events[0].payload.subject_id == "write_file"
        assert (await leases.snapshot()).revision == 4  # revision actually grew

    asyncio.run(exercise())
```

2. Add the failure-path test:

```python
def test_failed_write_tool_does_not_bump_revision_or_emit() -> None:
    async def exercise() -> None:
        provider = FakeProvider(*_script("write_file"))
        tool = WriteTool("write_file")
        tool.raises = True  # FakeTool.execute raises RuntimeError -> engine FAILED result
        leases = WorkspaceLeaseManager("C:/ws", revision=3)
        engine, bus = _engine(provider, FakeTools(tool), leases)
        accepted = await engine.submit(TurnRequest("go", SessionId("session-1")))
        assert accepted.value is not None
        result = await engine.wait(accepted.value.turn_id, 2)
        assert result.value is not None and result.value.status is TurnStatus.FAILED
        assert not [event for event in (await bus.snapshot()).events if event.event_type is EventType.WORKSPACE_CHANGED]
        assert (await leases.snapshot()).revision == 3  # untouched

    asyncio.run(exercise())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/engine/test_workspace_change_events.py -q`
Expected: FAIL — the first test asserts `revision == 4` but gets `3`; the second asserts `revision == 3` after a failure which currently... also fails only because the failure path currently does not bump (so the second test may pass trivially — run the first as the discriminating failure: the engine currently emits the pre-bump `3`).

- [ ] **Step 3: Implement**

1. `kairo_kernel/runtime/workspace.py` — add `bump_revision` to `WorkspaceLeaseManager` (after `update`, `:70-75`):

```python
    async def bump_revision(self) -> WorkspaceSnapshot:
        """Atomically advance the revision by one without changing the root."""
        lease = await self.write()
        async with lease:
            return await self.update(lease, lease.snapshot.root)
```

2. `kairo_kernel/engine/turns.py` — replace the emit block in `_execute_tool` (`:667-677`):

```python
        if (
            result.status is ToolExecutionStatus.SUCCEEDED
            and self.workspace_leases is not None
            and _mutates_workspace(run.snapshot.tools, call.name)
        ):
            workspace = await self.workspace_leases.bump_revision()
            await self._emit(
                run,
                EventType.WORKSPACE_CHANGED,
                ChangeEvent(workspace.revision, call.name, "Workspace files changed."),
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/engine/test_workspace_change_events.py -q`
Expected: `3 passed`.

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `304 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 8: MCP facade ToolGate (authorization + approval + timeout + fail-closed disconnect)

**Purpose:** The public `kernel.mcp` facade must never touch `McpClient.call_tool/read_resource/get_prompt` without passing through the kernel ToolGate: resolve the runtime mode from preferences, classify scope, enforce `AuthorizationPolicy`, request a blocking `TOOL_APPROVAL` interaction on the shared broker when policy denies (manual and auto both; safe default REJECT), execute only on approval, bound every call with `asyncio.wait_for`, and map timeout/disconnect to typed failures.

**Design decisions (locked):**

- **Mode + scope:** `_authorize` reads `parts.preferences.snapshot().authorization_mode` (the same snapshot the engine resolves at accept time). All three operations are classified `OperationScope.EXTERNAL` — `call_tool` mirrors `McpTool.classify` exactly (`tools/mcp.py:35-37`), and `read_resource`/`render_prompt` are **also** EXTERNAL because they contact an external server (the same classification `WebFetchTool._classify` uses for remote reads, `tools/web.py:65-67`); connect-time trust is transport-level and does not by itself authorize calls.
- **Approval outside a turn:** the facade is outside any turn, so approval uses the shared `InteractionBroker` with a **synthetic turn identity** (`TurnId`/`SessionId` from `uuid4`). The broker correlates purely by `interaction_id`/`turn_id` and resolves via `kernel.interactions.respond`; the interaction appears in `kernel.interactions.pending()` and in the `INTERACTION requested` event but **not** in `kernel.active_turns()` (that filters by supervisor), which is honest. On expiry/cancel/shutdown the broker returns its safe default, forced to REJECT. No new public API is added.
- **Choices** mirror the engine `TOOL_APPROVAL` set (`engine/turns.py:603-617`): `APPROVE_ONCE`, `REJECT`, `STOP` (treated as a plain rejection here — there is no task to stop), and `ENABLE_YOLO`/`ENABLE_AUTO` (which also persists the durable mode via `preferences.apply_authorization`, matching `_apply_authorization`).
- **Timeout:** `asyncio.wait_for(..., self._timeout_seconds)` around the client call; `TimeoutError` → `KernelError(RESOURCE_EXHAUSTED, retryable=True)`. Timeout value comes from a new additive `KernelConfig.mcp_call_timeout_seconds` (threaded through `_KernelParts` → `_Mcp`) so tests can set it small.
- **Disconnect:** a closed stdio stream surfaces as `McpProtocolError` (transport `:83-90`); after the client's single reconnect attempt fails it propagates and `_mcp_error` maps it to `PROVIDER_CLIENT` (typed, fail-closed; unchanged mapping).
- **Engine path out of scope:** `McpTool.execute` (in-turn MCP tools) is already authorized by the engine; its own timeout gap is documented as a remaining limitation in Task 9. This task gates only the public facade.

**Files:**
- Modify: `kairo_kernel/kernel.py` (imports, `_KernelParts`, `_Mcp`, `KairoKernel.__init__`)
- Modify: `kairo_kernel/factory.py` (`KernelConfig.mcp_call_timeout_seconds`, wiring)
- Modify: `tests/kernel/mcp/test_facade.py`

**Interfaces:**
- Consumes: `PreferencesService.snapshot()`, `TurnEngine.authorization` (`AuthorizationPolicyPort`), `InteractionBroker.request(request, cancellation)`, `CancellationSource` (`kairo_kernel/runtime/cancellation.py:38`), `InteractionRequest/InteractionChoice/InteractionResponse`, `_mcp_error`, `_freeze_mcp_result`, `_client_for`.
- Produces (all additive):
  - `KernelConfig.mcp_call_timeout_seconds: float = 30.0` (trailing field; validated `> 0` in `__post_init__`).
  - `_KernelParts.mcp_call_timeout_seconds: float = 30.0` (trailing field before `restore_provider_catalog`).
  - `_Mcp.__init__(kernel, hub, *, timeout_seconds: float = 30.0)`; `_Mcp._authorize(operation: str, scope: OperationScope) -> KernelResult[bool]` (private).
  - `call_tool`/`read_resource`/`render_prompt` now: lifecycle gate → client lookup (NOT_FOUND before authorization) → `_authorize("mcp.*", OperationScope.EXTERNAL)` → `asyncio.wait_for` → typed result; `_authorize` emits `INTERACTION requested/resolved` events and, on ENABLE_*, a `CONFIG_CHANGED` event.

**Steps**

- [ ] **Step 1: Write the failing tests** — rewrite the helper and add tests in `tests/kernel/mcp/test_facade.py`. New imports:

```python
from kairo_kernel.contracts.enums import AuthorizationMode, InteractionAction, InteractionKind
from kairo_kernel.contracts.interactions import InteractionRequest, InteractionResponse
from kairo_kernel.engine import EngineOptions
from kairo_kernel.mcp import McpProtocolError
```

Change the `_kernel` helper:

```python
def _kernel(
    tmp_path: Path,
    *,
    mode: AuthorizationMode = AuthorizationMode.MANUAL,
    transport_factory: object | None = None,
    timeout_seconds: float = 30.0,
):
    config = McpServerConfig("server", "stdio", command="echo-server")
    store = McpServerTrustStore(tmp_path / "mcp.json")
    store.trust(config, config.digest)
    client = McpClient(config, store, transport_factory=transport_factory or (lambda server: MemoryTransport()))
    root = str(tmp_path)
    return build_kernel(
        KernelConfig(
            root,
            database_path=root + "/kernel.db",
            default_session_id=SessionId("session-1"),
            enable_builtin_tools=False,
            engine_options=EngineOptions(authorization_mode=mode),
            mcp_call_timeout_seconds=timeout_seconds,
        ),
        KernelDependencies(
            provider=FakeProvider((ProviderStreamEvent(ProviderStreamKind.COMPLETED),)),
            sessions=FakeSessions(session()),
            mcp=McpHub((client,)),
        ),
    )
```

Change `test_mcp_facade_typed_calls_and_tool_bridge` to run under YOLO (unchanged calls, but without approval the default MANUAL mode would now block):

```python
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO)
```

Add the transport fakes after `MemoryTransport`:

```python
class RecordingTransport(MemoryTransport):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, message: dict[str, object]) -> dict[str, object]:
        self.calls.append(message["method"])
        return await super().request(message)


class HangingTransport(MemoryTransport):
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "tools/call":
            await asyncio.Future()  # never resolves
        return await super().request(message)


class ClosingTransport(MemoryTransport):
    async def request(self, message: dict[str, object]) -> dict[str, object]:
        if message["method"] == "tools/call":
            raise McpProtocolError("MCP stdio server closed the stream.")
        return await super().request(message)
```

Add a pending-wait helper and the seven tests:

```python
async def _wait_for_pending(kernel) -> InteractionRequest:
    for _ in range(200):
        pending = await kernel.interactions.pending()
        if pending:
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no pending interaction")


def test_mcp_facade_manual_blocks_until_approval_no_bypass(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(
                kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hello")))
            )
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            assert "tools/call" not in transport.calls  # no bypass: nothing reached the server yet
            receipt = await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            assert receipt.ok
            result = await task
            assert result.ok and result.value is not None
            assert "echo:" in str(result.value.get("content"))
            assert "tools/call" in transport.calls

    asyncio.run(exercise())


def test_mcp_facade_manual_reject_is_policy_denied(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.call_tool("mcp__server__tools__echo"))
            pending = await _wait_for_pending(kernel)
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.REJECT)
            )
            result = await task
            assert result.error is not None and result.error.code is ErrorCode.POLICY_DENIED
            assert "tools/call" not in transport.calls

    asyncio.run(exercise())


def test_mcp_facade_yolo_executes_without_approval(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            called = await kernel.mcp.call_tool("mcp__server__tools__echo", JsonObject.from_pairs(("text", "hi")))
            assert called.ok and called.value is not None
            assert "echo:" in str(called.value.get("content"))
            assert (await kernel.interactions.pending()) == ()  # no interaction was created

    asyncio.run(exercise())


def test_mcp_facade_auto_requires_approval_for_external_scope(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.AUTO, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.call_tool("mcp__server__tools__echo"))
            pending = await _wait_for_pending(kernel)
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            result = await task
            assert result.ok and result.value is not None

    asyncio.run(exercise())


def test_mcp_facade_reads_are_gated_in_manual_mode(tmp_path: Path) -> None:
    async def exercise() -> None:
        transport = RecordingTransport()
        kernel = _kernel(tmp_path, mode=AuthorizationMode.MANUAL, transport_factory=lambda server: transport)
        async with kernel:
            await kernel.mcp.connect()
            task = asyncio.create_task(kernel.mcp.read_resource("mcp__server__resources__guide"))
            pending = await _wait_for_pending(kernel)
            assert pending.kind is InteractionKind.TOOL_APPROVAL
            await kernel.interactions.respond(
                InteractionResponse(pending.interaction_id, pending.turn_id, InteractionAction.APPROVE_ONCE)
            )
            result = await task
            assert result.ok and "guide body" in str(result.value.get("contents"))

    asyncio.run(exercise())


def test_mcp_facade_call_tool_timeout_fails_closed(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(
            tmp_path,
            mode=AuthorizationMode.YOLO,
            transport_factory=lambda server: HangingTransport(),
            timeout_seconds=0.1,
        )
        async with kernel:
            await kernel.mcp.connect()
            result = await kernel.mcp.call_tool("mcp__server__tools__echo")
            assert result.error is not None and result.error.code is ErrorCode.RESOURCE_EXHAUSTED
            assert result.error.retryable

    asyncio.run(exercise())


def test_mcp_facade_disconnect_fails_closed(tmp_path: Path) -> None:
    async def exercise() -> None:
        kernel = _kernel(tmp_path, mode=AuthorizationMode.YOLO, transport_factory=lambda server: ClosingTransport())
        async with kernel:
            await kernel.mcp.connect()
            result = await kernel.mcp.call_tool("mcp__server__tools__echo")
            assert result.error is not None and result.error.code is ErrorCode.PROVIDER_CLIENT

    asyncio.run(exercise())
```

(There is no existing `interactions` import in `test_facade.py`; add both names on one line as shown above.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/mcp/test_facade.py -q`
Expected: FAIL — the gating tests hang or error (`kernel.mcp.call_tool` proceeds without authorization; `_wait_for_pending` raises `AssertionError: no pending interaction`), and `KernelConfig` rejects the unknown keyword `mcp_call_timeout_seconds`.

- [ ] **Step 3: Implement**

1. `kairo_kernel/factory.py` — add the config field after `enable_builtin_tools` (`:91`) and validate it in `__post_init__` (`:93-101`):

```python
    mcp_call_timeout_seconds: float = 30.0
```

```python
        if self.mcp_call_timeout_seconds <= 0:
            raise ValueError("mcp_call_timeout_seconds must be positive.")
```

and pass it through in `_KernelParts(...)` (add `mcp_call_timeout_seconds=config.mcp_call_timeout_seconds,` after `preferences=preferences,` at `:235`).

2. `kairo_kernel/kernel.py` — update imports:

```python
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
```

```python
from kairo_kernel.contracts.enums import (
    AuthorizationMode,
    ErrorCode,
    EventType,
    InteractionKind,
    LifecycleState,
    OperationScope,
    TurnStatus,
)
```

```python
from kairo_kernel.contracts.events import ChangeEvent, EventReplay, InteractionEvent, LifecycleEvent
```

```python
from kairo_kernel.contracts.identifiers import InteractionId, KernelId, MemoryId, ProfileId, SessionId, TurnId
```

```python
from kairo_kernel.contracts.interactions import (
    InteractionChoice,
    InteractionReceipt,
    InteractionRequest,
    InteractionResponse,
)
```

```python
from kairo_kernel.runtime import (
    AsyncLifecycle,
    CancellationSource,
    EventBus,
    EventSubscription,
    InteractionBroker,
    SessionTurnSupervisor,
    WorkspaceLeaseManager,
)
```

3. `_KernelParts` — add the trailing field before `restore_provider_catalog` (`:109`):

```python
    mcp_call_timeout_seconds: float = 30.0
    restore_provider_catalog: bool = False
```

4. `KairoKernel.__init__` — pass the timeout to the facade (`:132`):

```python
        self.mcp = _Mcp(self, parts.mcp, timeout_seconds=parts.mcp_call_timeout_seconds)
```

5. `_Mcp` — new constructor and gate; replace `call_tool` (`:818-832`), `read_resource` (`:834-847`), `render_prompt` (`:849-863`):

```python
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
        return KernelResult.success(await self._hub.connect_all())

    async def refresh(self) -> KernelResult[tuple[McpCatalog, ...]]:
        error = self._kernel._mutation_error("mcp.refresh")
        if error is not None:
            return KernelResult.failure(error)
        return KernelResult.success(await self._hub.refresh_all())

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
```

(Note: the `McpCatalog` return annotation of `connect`/`refresh` requires `McpCatalog` in the existing `from kairo_kernel.mcp import ...` import — it is already there. `STOP` responses fall through to the final `POLICY_DENIED` failure, which is the facade-appropriate equivalent of the engine's `_TurnCancelled`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel/mcp/test_facade.py -q`
Expected: PASS — the two existing tests (one under YOLO) plus the seven new ones.

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `311 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, suite result. No commit.

---

## Task 9: Docs + full gate

**Purpose:** Update the kernel docs to match the new behaviors, remove the now-fixed limitation, document the remaining MCP engine-path gap, and run the complete gate one final time.

**Files:**
- Modify: `docs/kernel/limitations.md`
- Modify: `docs/kernel/public-api.md` (CRLF — Edit preserves it)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: nothing (documentation only).

**Steps**

- [ ] **Step 1: Update `docs/kernel/limitations.md`** — remove item 1 (the port mismatch, now fixed by Task 3) and rewrite item 12 (config concurrency, now single-process locked). Replace the whole list body (`:3-42`) with the renumbered list:

```markdown
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
```

- [ ] **Step 2: Update `docs/kernel/public-api.md`** — three targeted edits (CRLF file; Edit preserves the endings):

1. In the `### kernel.mcp` section (`:135-142`), after the catalog-bridge paragraph, add:

```
All three MCP calls pass through the facade ToolGate: the runtime mode is
resolved from preferences, external scope in manual/auto raises a
`TOOL_APPROVAL` interaction (approved via `kernel.interactions.respond`;
safe default reject), yolo executes directly, each call is bounded by
`KernelConfig.mcp_call_timeout_seconds`, and timeout/disconnect fail closed
as `RESOURCE_EXHAUSTED` (retryable) / `PROVIDER_CLIENT`.
```

2. In the `### kernel.workspace` section (`:108-117`), after the `move` bullet, add:

```
`move` returns retryable `KERNEL_BUSY` while any turn is active.
```

3. In the configuration-document section (`:162-171`), after the first paragraph, add:

```
`store.update(expected_revision, transform)` mutates the document under a
single-process lock and advances its persisted `revision`; stale writers
receive `CONFLICT`.
```

- [ ] **Step 3: Update `CHANGELOG.md`** — add a `### Fixed / 修复` block under `[0.4.0a2]` (`:3-16`):

```markdown
### Fixed / 修复

- 公共 MCP 调用统一走 ToolGate：manual/auto 需人工批准、yolo 直通，调用带超时且断连/超时均失败关闭；工作区移动在有 turn 运行时返回可重试的 `KERNEL_BUSY`，工作区修订号仅在写/执行工具成功后递增。
  Public MCP calls now pass through the ToolGate (manual/auto require approval, yolo executes directly, every call is timeout-bounded and fails closed); workspace moves return retryable `KERNEL_BUSY` while a turn is active, and the workspace revision advances only on write/execute tool success.
- `KernelConfigStore.update(expected_revision, transform)` 以单进程锁 + 乐观修订号协调配置写入；`contracts.commands` / `contracts.preferences` 与 `ports.PreferencesPort` 进入公共导出，`PreferencesPatch.clear_profile_id` 可显式清除 profile 覆盖。
  `KernelConfigStore.update(expected_revision, transform)` coordinates config writes with a single-process lock and optimistic revisions; `contracts.commands`, `contracts.preferences` and `ports.PreferencesPort` are public exports, and `PreferencesPatch.clear_profile_id` explicitly clears the profile override.
```

- [ ] **Step 4: Run the full gate (final)**

Run: `.venv/Scripts/python.exe -m pytest tests/kernel -x -q` → Expected: `311 passed`.
Run: `ruff check kairo_kernel tests/kernel` → Expected: `All checks passed!`.
Run: `mypy kairo_kernel` → Expected: `Success: no issues found in N source files`.

**Report:** files changed, final suite count. No commit — the phase ends here; the user reviews and commits.
