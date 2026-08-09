# Workbench Gate — TDD Implementation Plan

Date: 2026-08-08 · Phase: 实施顺序 step 4 (tui_plan.md) · Deliverable: `kairo-tui` Workbench pages

## Goal

Ship the remaining workbench pages of the new TUI on top of the landed Foundation + Chat Gate:

- **Page shell refactor**: `_show_page` mounts one screen per page (Sessions/Workspace/Memory/Extensions/Settings/Doctor); nav buttons gain real `on_button_pressed` handlers (carry-forward); `Ctrl+1…7` already bound.
- **Sessions page**: list/search/switch/rename/delete/export + clear/undo/compress (via `kernel.commands` where a command exists, facade otherwise); running sessions carry a ● badge; switching selects `active_session_id` and never cancels turns.
- **Workspace page**: lazy directory tree, text preview, Git changed files + diff, bookmarks, workspace switch; **every read result is dropped when its revision is stale**; `move` is gated BUSY while turns run (the error is surfaced).
- **Memory page**: namespace/tag/text search, view, create, edit, delete.
- **Extensions page**: tabs Built-in Tools / Skills / MCP; list + reload/connect/refresh; trust/revoke (digest) for skills; typed MCP invocation with approval surfacing (pending interaction → Activity tab; no auto-approval).
- **Settings page**: profiles CRUD + role routing + keyring secrets (SecretStore only, env fallback), authorization/Plan/Thinking/context, theme/animation/keybindings written to the ConfigDocument and **theme applied to Textual**, recent workspace.
- **Doctor page**: local/full diagnostics with per-check status, retry, cancel, **copy-redacted report** (the TUI redacts — the kernel does not).
- **Unified command registry completion**: navigation, command palette and slash share one registry; business commands delegate to `kernel.commands` with the active session id.
- **Multimedia ContentBlock metadata cards**: media blocks render as metadata cards; nothing auto-opens external programs; save/open are explicit.
- **Carry-forward fixes**: `_key_for` multi-block reasoning collision (fix now — the defer is lifted), theme application, `setup_complete`/theme-wipe timing in Setup, Inspector Changes tab (WORKSPACE_CHANGED events with revision), delete the dead `screens/workbench.py`.

End state (all green): `pytest frontends/tui/tests -q` = **236**, `pytest tests/kernel -q` = **316** (kernel untouched — API 1.1 frozen), `ruff check frontends/tui` clean, `mypy frontends/tui` clean.

## Architecture

```
Nav buttons / Ctrl+1..7 / slash / palette ──▶ KairoTuiApp.action_page ──▶ _show_page ──▶ per-page Screen in #page
                                                       │
Every page:  Screen (Textual) ── kernel.* facade ──▶ KernelResult / DTO
     │               ▲
     │  store.subscribe + pure view-models (kairo_tui/*_model.py, no Textual)
     ▼
AppStore (typed reducer) ◀── EventPump (events + recovery)
```

- Each page is a `Container` subclass mounted into `#page` by `_show_page`; screens follow the existing `ChatScreen`/`SetupScreen` convention (`self._app`, `self.kernel`, `self.store`).
- **Pure logic lives outside Textual** in new `*_model.py` modules so unit tests never construct widgets (same rule as `chat_model.py`).
- The store remains the single source of truth: pages re-read kernel DTOs for their own mutations and dispatch typed actions (`SessionsAction`, `WorkspaceAction`, …) instead of deriving business state.
- **DTOs whose types live in AST-forbidden kernel modules** (workspace, skills, mcp catalog, diagnostics) are accessed **structurally** through TUI-local Protocols in `kairo_tui/structs.py` + `cast(...)` — the pattern `event_pump.py` already uses for `workspace.snapshot()`.

## Tech Stack

- `textual>=8.2,<9` — verified 8.2.8 installed. Uses `TabbedContent`, `Tree` (lazy expansion via `NodeExpanded` — `textual.widgets.Tree` verified), `Select`, `Switch`, `Input`, `ModalScreen`, `App.copy_to_clipboard(text)` (verified present), `App.bind(keys, action)` (verified), `App.available_themes` / `App.theme` (verified). **Note: Textual 8.2.8 has no `Slider` widget** (verified: not in `textual.widgets.__all__`, no `textual.widgets.slider` module) — numeric settings use validated `Input`s.
- `kairo-kernel` public surface only (AST boundary, enforced by `test_boundaries.py`): `kairo_kernel`, `kairo_kernel.contracts.*`, `kairo_kernel.ports.*`, `kairo_kernel.errors`.
- pytest + Textual Pilot (`asyncio.run(drive())` pattern from `test_chat_screen.py`/`test_inspector.py`).

## Global Constraints

1. **Public kernel API only** — no imports of `kairo_kernel.engine|services|runtime|factory|kernel|mcp|memory|providers|skills|storage|config_document|_version`; DTOs from those modules are read **structurally** (`structs.py` Protocols + `cast`).
2. **Revision-stale drop** — every workspace read result (`tree/preview/changed_files/diff`) is dropped unless `root == store.workspace_root` **and** `revision == store.workspace_revision`; mutations pass `expected_revision` and surface `CONFLICT`/`KERNEL_BUSY`.
3. **No auto-approval** — MCP typed invocation may produce a pending interaction (synthetic identity on the shared broker); the TUI only surfaces it (Activity tab + review affordance) and never responds on expiry.
4. **Secrets via SecretStore only** — the Settings/Doctor pages touch secrets exclusively through `kairo_tui.keyring_store.SecretStore` (keyring first, `KAIRO_SECRET_*` env reference fallback); no plaintext to disk, no secret in repr/logs/exports.
5. **Redacted doctor reports** — the kernel's `DiagnosticReport` is **not** redacted (verified: checks carry raw `message`/`details`); the TUI redacts on copy via `kairo_tui/redaction.py` with markers resolved from the SecretStore + `KAIRO_SECRET_*` env vars.
6. **Theme applied** — `ConfigDocument.theme` is applied to Textual (`app.theme`) at startup and on document change; `"default"` → `"textual-dark"`, unknown names fall back with a notify.
7. **No `typing import Any`**; `mypy frontends/tui` clean (whole tree); `ruff check frontends/tui` clean; line length 120.
8. **No git commits.**
9. All collections stay keyed by kernel IDs (session/turn/message/tool/interaction/memory) — never match display text.
10. Kernel is untouched this gate (API 1.1 frozen) — `tests/kernel` stays at 316.

## Evidence & decisions (verified against the real code)

### Page shell today
`app.py:162` `_show_page` mounts only `ChatScreen`/`SetupScreen`; every other page mounts a `Static("… wired in a later gate.")`. Nav buttons are rendered (`app.py:145-152`) **without** handlers; `Ctrl+1…7` bindings already call `action_page` (`app.py:42-49`). `screens/workbench.py` (`WorkbenchScreen`) is dead code — nothing imports it (only `screens/__init__.py` docstring mentions it); it is deleted in Task 1.

### Store freshness gaps found
- `store.sessions` is populated **only** by `RecoveryAction` and manual `SessionsAction` dispatches (`fold_event` handles only Turn/Message/Tool/Interaction/ChangeEvent(WORKSPACE)). After bootstrap and after any facade mutation, `state.sessions` is stale. Every page that shows sessions must re-list via `kernel.sessions.list()` and dispatch `SessionsAction` on mount and after mutations. `app._new_chat` has the same gap (dispatch `SessionsAction` too).
- `app._run_command` executes kernel commands with `session_id=None` (`app.py:320`), which fails every `needs_session` command (`/clear /undo /compress /export`). Task 8 fixes it to pass the store's active session id.

### Workspace revision contract
`WorkspaceTree/WorkspacePreview/WorkspaceDiff/ChangedFiles` all carry `root` + `revision` (`services/workspaces.py:26-84`). `store.fold_event` already bumps `workspace_revision` on `WORKSPACE_CHANGED` (`store.py:268-269`), and `RecoveryAction` re-reads the workspace snapshot. `move(target, expected_revision)` returns `KERNEL_BUSY` when a turn is active (`services/workspaces.py:444-455`) and `CONFLICT` on revision mismatch — both surfaced, never swallowed.

### `kernel.configuration.patch` is AST-unusable
`kernel.configuration.snapshot()` returns `ConfigSnapshot` (public contract, `contracts/support.py:33-38`, `redacted=True`). But `patch()` takes `ConfigPatch`/`ConfigChange` defined in `kairo_kernel/services/configuration.py` — a forbidden import, and API 1.1 is frozen so they cannot move to `contracts` this gate. **Decision:** the Settings page shows the per-workspace SQLite config **read-only** (snapshot values) and does not patch it from the TUI. Escalated (see "Escalated ambiguities") — a future kernel gate should add a public config-patch contract if raw config editing is ever required.

### DiagnosticReport redaction source
`services/diagnostics.py:53-80`: `DiagnosticReport(mode, checks, duration_ms)`; checks carry `message` + `details` verbatim from probes (`_safe_error` only strips exception names). `kernel.diagnostics.local()/full()` return it wrapped in `KernelResult`. **The kernel does NOT redact → the TUI redacts on copy.** Redaction needs the secret values, so `redaction.py` resolves them from the `SecretStore` (keyring/env) at copy time — the values flow into memory only to be masked (consistent with `test_secret_scan.py`: no full key material in exports).

### `_key_for` multi-block reasoning collision (defer lifted)
`chat_model.session_timeline` emits **one `ReasoningItem` per ReasoningBlock with the same `message_id`** (a message with `(ReasoningBlock("step 1"), TextBlock("answer"), ReasoningBlock("step 2"))` yields two items both keyed `"m1"` — `test_chat_model.py:153` proves the model side). `ChatScreen._key_for` returns `item.message_id` for `ReasoningItem` (`chat.py:601-610`), so the second reasoning item silently overwrites the first in `_widgets` — only one Collapsible renders. Fix: give `ReasoningItem` a per-message `index` and key by `f"{message_id}#{index}"`. (`TextItem` concatenates all text blocks into one item, `PlanItem` takes over the whole message — no index needed there.)

### `setup_complete`/theme wipe
`SetupScreen._persist` (`screens/setup.py:160-172`) rebuilds the document from the live catalog **dropping `theme`, `keybindings`, `recent_workspaces`** — finishing setup wipes `--theme`. Fix: `dataclasses.replace(self.store.state.document, profiles=…, roles=…, default_profile_id=…)`. (No nav gating on `setup_complete` — `test_ctrl_b_navigates_workspace` in `test_toggles.py` navigates to WORKSPACE with an empty config, so `action_page` stays ungated.)

### Unified registry dual meanings
`/workspace`, `/memory`, `/doctor` exist as **both** TUI nav commands and kernel business commands (kernel: workspace move / memory search / diagnostics). Resolution: bare word → nav; with arguments → kernel business command (`parse_tui_command` returns `None` when args present so `_run_command` falls through to `kernel.commands.parse`). `/sessions` (kernel list, no args) → nav (the page shows the same list). `/settings`, `/extensions`, `/chat`, `/help`, `/setup`, `/exit` → TUI-only.

### Extensions structural DTOs
`ToolDescriptor` is a public contract (`contracts/tools.py:15`). `SkillInventory`/`SkillPackage` (`skills/registry.py`), `CatalogEntry`/`McpCatalog` (`mcp/models.py`) and `McpHub.clients` are in forbidden modules → `structs.py` Protocols. `kernel.mcp.catalog()` is synchronous and may raise `McpProtocolError`; the facade does not wrap it (`kernel.py:827-828`), so the TUI guards the call with a broad `except Exception` → error banner. `kernel.skills.inspect()` returns the inventory **directly** (not a `KernelResult`); `reload/trust/revoke` return `KernelResult`.

### Theme naming
Verified `App.available_themes` in the installed Textual: `ansi-dark/light, atom-one-dark/light, catppuccin-frappe/latte/macchiato/mocha, dracula, flexoki, gruvbox, monokai, nord, rose-pine(-dawn/-moon), solarized-dark/light, textual-dark/light, tokyo-night`. Mapping: `"default"` → `"textual-dark"`, everything else passes through (fallback + notify on unknown).

### Test harness
`frontends/tui/tests/conftest.py` builds a **real kernel** via public `build_kernel` with a `FakeProvider`/`FakeToolRegistry` seam; Pilot tests bootstrap synchronously outside the loop and drive inside `asyncio.run` (documented in `test_commands.py`). New page tests follow the same fixture pattern (`page_app_factory` per page, seeded `ConfigDocument` at `workspace.parent / "config-v1.json"`).

## Baseline & accounting

| Suite | Baseline | Per-task additions | Final |
|---|---|---|---|
| `frontends/tui/tests` | 126 | T1 +10 · T2 +14 · T3 +16 · T4 +10 · T5 +14 · T6 +14 · T7 +10 · T8 +8 · T9 +6 · T10 +8 · T11 +0 | **236** |
| `tests/kernel` | 316 | — (kernel untouched) | **316** |

---

## Task 1 — Page shell: per-page screens, nav button handlers, arg-aware TUI command routing

The shell is the dependency of every later page task, so it lands first: `_show_page` mounts one screen per page; the nav `Button`s gain handlers; `parse_tui_command` becomes arg-aware; the dead `screens/workbench.py` is deleted.

**Files**
- `frontends/tui/kairo_tui/screens/sessions.py`, `screens/workspace.py`, `screens/memory.py`, `screens/extensions.py`, `screens/settings.py`, `screens/doctor.py` (new — thin scaffolds this task, bodies replaced by Tasks 2–7)
- `frontends/tui/kairo_tui/app.py` (`_show_page`, `on_button_pressed`, `_render_nav`)
- `frontends/tui/kairo_tui/commands.py` (`ARG_AWARE_COMMANDS`, arg-aware `parse_tui_command`)
- delete `frontends/tui/kairo_tui/screens/workbench.py`; update `screens/__init__.py` docstring
- tests: `frontends/tui/tests/test_page_shell.py` (new), `frontends/tui/tests/test_commands.py` (extend), `frontends/tui/tests/test_app_layout.py` (extend), `frontends/tui/tests/test_boundaries.py` (extend)

**Interfaces — Consumes** (all real): `PageId` (store), `kernel.commands.parse/execute`, `ConfigDocument` (no reads this task).

**Interfaces — Produces**

```python
# screens/sessions.py  (scaffold; full body in Task 2)
class SessionsScreen(Container):
    def __init__(self, app) -> None:
        super().__init__(id="sessions-screen")
        self._app = app
        self.kernel = app.kernel
        self.store = app.store
    def compose(self) -> ComposeResult:
        yield Static("[b]Sessions[/b]", id="sessions-title")
        yield Static("wired.", id="sessions-body")
```

(Workspace/Memory/Extensions/Settings/Doctor scaffolds identical with `id="workspace-screen"` etc.)

**Steps**

1. Add the six screen scaffolds above.
2. Rewrite `_show_page` to mount the per-page screen:

```python
def _show_page(self, page: PageId) -> None:
    if self._current_page is page:
        return  # already shown; mounting twice in one turn would duplicate ids
    self._current_page = page
    container = self.query_one("#page", Container)
    container.remove_children()
    if page is PageId.CHAT:
        from kairo_tui.screens.chat import ChatScreen
        container.mount(ChatScreen(self))
    elif page is PageId.SETUP:
        from kairo_tui.screens.setup import SetupScreen
        container.mount(SetupScreen(self))
    elif page is PageId.SESSIONS:
        from kairo_tui.screens.sessions import SessionsScreen
        container.mount(SessionsScreen(self))
    elif page is PageId.WORKSPACE:
        from kairo_tui.screens.workspace import WorkspaceScreen
        container.mount(WorkspaceScreen(self))
    elif page is PageId.MEMORY:
        from kairo_tui.screens.memory import MemoryScreen
        container.mount(MemoryScreen(self))
    elif page is PageId.EXTENSIONS:
        from kairo_tui.screens.extensions import ExtensionsScreen
        container.mount(ExtensionsScreen(self))
    elif page is PageId.SETTINGS:
        from kairo_tui.screens.settings import SettingsScreen
        container.mount(SettingsScreen(self))
    elif page is PageId.DOCTOR:
        from kairo_tui.screens.doctor import DoctorScreen
        container.mount(DoctorScreen(self))
```

3. Add the nav handler in `KairoTuiApp` (carry-forward):

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    button_id = event.button.id or ""
    if not button_id.startswith("nav-"):
        return
    self.action_page(button_id.removeprefix("nav-"))
```

4. Arg-aware routing in `commands.py`:

```python
# Words that double as kernel business commands: bare → nav, with args → kernel.
ARG_AWARE_COMMANDS = frozenset({"/workspace", "/memory", "/doctor"})


def parse_tui_command(text: str) -> ParsedCommand | None:
    """Return a ParsedCommand when the text names a TUI command, else None.

    Arg-aware: /workspace|/memory|/doctor with arguments fall through to the
    kernel business commands (workspace move / memory search / diagnostics).
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    tokens = stripped.split()
    name = tokens[0]
    if name not in TUI_COMMANDS:
        return None
    if name in ARG_AWARE_COMMANDS and len(tokens) > 1:
        return None
    return ParsedCommand(name, tuple(tokens[1:]))
```

5. Delete `screens/workbench.py`; update `screens/__init__.py` docstring to "Screens: page shells, inspector, setup wizard."
6. **TDD tests** (write first, then implement):
   - unit ×3 (`test_commands.py`): `/workspace <path>` → `None`; `/memory ns text` → `None`; `/doctor full` → `None`; bare `/workspace`, `/memory`, `/doctor` still nav.
   - Pilot ×5 (`test_page_shell.py`, `page_app_factory` seeded config): clicking `#nav-settings` mounts `#settings-screen` and sets `store.state.page`; `ctrl+2`/`ctrl+7` mount `#sessions-screen`/`#doctor-screen`; navigating away and back mounts exactly one instance of each screen (no duplicate id errors); the seven nav buttons all render; the default page is Chat when setup is complete.
   - boundaries ×1: `test_workbench_screen_deleted` (mirrors `test_compat_screen_deleted`).
   - app_layout ×1: page switch does not disturb `#topbar`/`#inspector` mounts.

**Verification** — `pytest frontends/tui/tests -q` → **136** · `pytest tests/kernel -q` → **316** · `ruff check frontends/tui` · `mypy frontends/tui`.

---

## Task 2 — Sessions page: list/search/switch/rename/delete/export + clear/undo/compress + running badges

**Files**
- `frontends/tui/kairo_tui/sessions_model.py` (new, pure)
- `frontends/tui/kairo_tui/screens/sessions.py` (full body)
- `frontends/tui/kairo_tui/screens/chat.py` (no change — chips already consume `state.sessions`)
- `frontends/tui/kairo_tui/app.py` (`_new_chat` also dispatches `SessionsAction` after create)
- tests: `frontends/tui/tests/test_sessions_model.py` (new), `frontends/tui/tests/test_sessions_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.sessions.list()/search(text, limit=50)/create(name)/rename(id, name)/delete(id)/export(id, format="json")`, `kernel.conversations.clear(id)/undo(id)/compress(id, summary, preserve_recent_turns=4)`, `store.state.sessions/active_turns/active_session_id`, `SessionSummary(session_id, name, message_count, created_at, updated_at, context_used_tokens)`.

**Interfaces — Produces**

```python
# sessions_model.py
@dataclass(frozen=True)
class SessionRow:
    session_id: str
    name: str
    message_count: int
    updated_at: str
    running: bool
    active: bool


def session_rows(state: AppState) -> tuple[SessionRow, ...]:
    """Sessions newest-updated first, with running/active flags (badges)."""
    running = frozenset(str(turn.session_id) for turn in state.active_turns)
    active = state.active_session_id
    ordered = sorted(state.sessions, key=lambda s: s.updated_at, reverse=True)
    return tuple(
        SessionRow(
            str(s.session_id), s.name, s.message_count,
            s.updated_at.isoformat(timespec="seconds"),
            str(s.session_id) in running, str(s.session_id) == active,
        )
        for s in ordered
    )


def running_session_ids(state: AppState) -> frozenset[str]:
    return frozenset(str(turn.session_id) for turn in state.active_turns)
```

A shared helper so every page can keep `state.sessions` fresh (store gap documented above):

```python
# page.py (new — shared page utilities)
async def refresh_sessions(app) -> None:
    """Re-list sessions into the store after any session mutation."""
    result = await app.kernel.sessions.list()
    if result.ok and result.value is not None:
        app.store.dispatch(SessionsAction(result.value))
```

**Screen layout** (`screens/sessions.py`, id `sessions-screen`): title + `Input(id="sessions-search")` + action buttons row (`New`, `Rename`, `Delete`, `Export`, `Clear`, `Undo`, `Compress`) + `VerticalScroll(id="sessions-list")`. Each row is a `Button` (id `ses-{session_id}`) labelled `● name — N messages — updated` with `variant="primary"` when active. Store-driven re-render via `store.subscribe`; a modal (`SessionTextModal`) reuses the `PlanEditModal` pattern for rename/compress-summary inputs; the export path is `<workspace>/kairo_exports/session-{session_id}.{format}` (TUI-side file write, documented; `Path.write_text`).

**Behavioral rules**
- Mount → `refresh_sessions(app)`; after every create/rename/delete → `refresh_sessions(app)`.
- Switch (`ses-…` pressed) → `SessionAction(session_id)` only — no kernel call, background turns keep running (tui_plan: switching never cancels).
- `New` → `kernel.sessions.create("Chat")` → activate + `refresh_sessions`.
- `Rename` → modal input → `kernel.sessions.rename`.
- `Delete` → `kernel.sessions.delete`; if it was the active session, activate the first remaining session (or `None`).
- `Export` → `kernel.sessions.export(session_id, format="json")` → write file → `app.notify(path)`.
- `Clear`/`Undo` → `kernel.conversations.clear/undo(active session)`.
- `Compress` → modal summary → `kernel.conversations.compress(session_id, summary)`.
- Search Input → `kernel.sessions.search(text)` on debounced submit; results replace the store view for rendering (or filter `state.sessions` in the model — decision: **filter the store list locally** for instant feedback, since `search` also matches message content and is covered by the kernel command).

**TDD tests** — model unit ×3 (`test_sessions_model.py`): rows newest-first; running badge from `active_turns`; active flag from `active_session_id`. Pilot ×11 (`test_sessions_screen.py`): list renders after mount (auto `refresh_sessions`); search filters; switch selects `active_session_id` without cancelling a running turn (FakeProvider `block=True` turn in another session stays active); rename via modal persists to the kernel; delete removes + activates a survivor; export writes a JSON file containing the session id; clear empties the conversation; undo removes the latest turn; compress modal invokes `kernel.conversations.compress` (assert `compression_count`); running session shows a ● in the row label; empty state renders "No sessions."

**Verification** — `pytest frontends/tui/tests -q` → **150** · kernel **316**.

---

## Task 3 — Workspace page: lazy tree, preview, Git changed files + diff, bookmarks, switch, stale-revision drop

**Files**
- `frontends/tui/kairo_tui/workspace_model.py` (new, pure)
- `frontends/tui/kairo_tui/structs.py` (new — workspace DTO Protocols)
- `frontends/tui/kairo_tui/screens/workspace.py` (full body)
- tests: `frontends/tui/tests/test_workspace_model.py` (new), `frontends/tui/tests/test_workspace_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.workspace.snapshot()` (→ `WorkspaceState(root, revision, bookmarks)` — structural), `tree(relative_path, limit=200)` (→ `KernelResult[WorkspaceTree]`), `preview(relative_path)` (→ `KernelResult[WorkspacePreview]`), `changed_files()` (→ `KernelResult[ChangedFiles]`), `diff(relative_path, max_bytes=65_536)` (→ `KernelResult[WorkspaceDiff]`), `move(target, expected_revision)`, `save_bookmark(WorkspaceBookmark(name, path), expected_revision)`, `remove_bookmark(name, expected_revision)`; `store.state.workspace_root/workspace_revision`.

**Interfaces — Produces**

```python
# structs.py (structural DTO Protocols — attribute-compatible with the real DTOs)
class WorkspaceEntryLike(Protocol):
    name: str
    relative_path: str
    is_directory: bool
    size_bytes: int


class WorkspaceTreeLike(Protocol):
    root: str
    revision: int
    relative_path: str
    entries: tuple[WorkspaceEntryLike, ...]
    truncated: bool


class WorkspacePreviewLike(Protocol):
    root: str
    revision: int
    relative_path: str
    is_directory: bool
    size_bytes: int
    text: str
    children: tuple[str, ...]
    truncated: bool


class ChangedFileLike(Protocol):
    relative_path: str
    status: str


class ChangedFilesLike(Protocol):
    root: str
    revision: int
    is_git_repository: bool
    files: tuple[ChangedFileLike, ...]


class WorkspaceDiffLike(Protocol):
    root: str
    revision: int
    relative_path: str
    status: str
    unified_diff: str
    truncated: bool


class WorkspaceBookmarkLike(Protocol):
    name: str
    path: str


class WorkspaceStateLike(Protocol):
    root: str
    revision: int
    bookmarks: tuple[WorkspaceBookmarkLike, ...]
```

```python
# workspace_model.py
def is_stale(root: str, revision: int, state: AppState) -> bool:
    """Drop the response when the workspace moved or was mutated meanwhile."""
    return root != state.workspace_root or revision != state.workspace_revision


STATUS_LABEL = {"modified": "M", "added": "A", "deleted": "D", "renamed": "R", "untracked": "U"}


@dataclass(frozen=True)
class ChangedRow:
    relative_path: str
    label: str  # "M path" etc.


def changed_rows(result: ChangedFilesLike) -> tuple[ChangedRow, ...]:
    return tuple(ChangedRow(f.relative_path, f"{STATUS_LABEL.get(f.status, '?')} {f.relative_path}")
                 for f in result.files)
```

**Screen layout** (`workspace.py`, id `workspace-screen`): a `Tree` widget (`WorkspaceTreeWidget`, below) + a preview `Static` + a changed-files `VerticalScroll` of row buttons (id `chg-{path}`) + a diff `Static` + a bookmark `Input`/`Save`/list + a workspace-switch `Input`/`Move` + a status line. Store-driven stale-drop helper:

```python
async def _fetch_tree(self, relative_path: str) -> None:
    result = await self.kernel.workspace.tree(relative_path)
    if not result.ok or result.value is None:
        self._notice(result.error.message if result.error else "Tree unavailable.")
        return
    tree = cast(WorkspaceTreeLike, result.value)
    if is_stale(tree.root, tree.revision, self.store.state):
        return  # stale response dropped (tui_plan.md)
    self._render_entries(tree)
```

`WorkspaceTreeWidget(Tree)` — lazy expansion:

```python
class WorkspaceTreeWidget(Tree):
    def __init__(self, kernel, store) -> None:
        super().__init__("workspace", id="workspace-tree")
        self._kernel, self._store = kernel, store

    async def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        path = node.data or "."
        if node.children:
            return
        result = await self._kernel.workspace.tree(str(path))
        if not result.ok or result.value is None:
            return
        tree = cast(WorkspaceTreeLike, result.value)
        if is_stale(tree.root, tree.revision, self._store.state):
            return
        for entry in tree.entries:
            label = f"{entry.name}/" if entry.is_directory else entry.name
            child = node.add(label, data=entry.relative_path)
            if entry.is_directory:
                child.add_leaf("…")  # placeholder so the arrow shows; replaced on expand
        if tree.truncated:
            node.add_leaf("… truncated …")
```

Root seeded from `kernel.workspace.tree(".")` on mount (stale-checked). File leaf selection → preview; changed-file selection → diff; bookmark Save → `kernel.workspace.save_bookmark(WorkspaceBookmark(name, path), expected_revision)`; Remove per bookmark → `remove_bookmark`; Move → `kernel.workspace.move(target, expected_revision)` — **surface `KERNEL_BUSY`/`CONFLICT`/invalid messages in the status line** (never silently dropped); on success dispatch `WorkspaceAction(root, revision)` from the returned `WorkspaceState` and record the recent workspace (Task 6 helper).

**TDD tests** — model unit ×2 (`test_workspace_model.py`): `is_stale` matrix (same root+rev → False; root or rev change → True); `changed_rows` labels. Pilot ×14 (`test_workspace_screen.py`, workspace fixture with `git init` + files): tree lists root entries; expanding a directory fetches children lazily (assert child node appears only after expand); file preview shows text; directory preview shows children; changed-files list renders git status rows; diff shows the unified diff; an untracked file shows the untracked marker (empty diff + "untracked"); bookmark save appears in the list; bookmark remove disappears; workspace switch (move to a second dir) updates `store.workspace_root`/`revision`; move while a turn runs (`FakeProvider(block=True)` + submit) → status line shows the BUSY error; a stale response is dropped (dispatch a newer `WORKSPACE_CHANGED` before the fetch lands → assert the pane did not update); non-git workspace → changed files shows a "not a git repository" notice (`is_git_repository` False); bookmark empty state.

**Verification** — `pytest frontends/tui/tests -q` → **166** · kernel **316**.

---

## Task 4 — Memory page: search/view/create/edit/delete

**Files**
- `frontends/tui/kairo_tui/memory_model.py` (new, pure)
- `frontends/tui/kairo_tui/screens/memory.py` (full body)
- tests: `frontends/tui/tests/test_memory_model.py` (new), `frontends/tui/tests/test_memory_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.memory.search(MemoryQuery(namespace, text, limit=20, tags=()))`, `get(memory_id)`, `put(MemoryEntry)`, `delete(memory_id)`; `MemoryEntry(memory_id, namespace, key, content, created_at, updated_at, tags)`, `TextBlock(text)`, `MemoryId(str)`; the kernel validates `namespace` non-empty and namespace/key uniqueness (CONFLICT surfaced).

**Interfaces — Produces**

```python
# memory_model.py
def build_query(namespace: str, text: str, tags: str, limit: int = 20) -> MemoryQuery:
    """Tags input splits on commas/whitespace into the MemoryQuery tags tuple."""
    parsed = tuple(t for t in tags.replace(",", " ").split() if t)
    return MemoryQuery(namespace.strip(), text.strip(), limit, parsed)


@dataclass(frozen=True)
class MemoryRow:
    memory_id: str
    namespace: str
    key: str
    preview: str  # first 60 chars of the text content
    tags: tuple[str, ...]


def memory_rows(entries: tuple[object, ...]) -> tuple[MemoryRow, ...]:
    # entries are MemoryEntry (public contract) — direct access is fine
    rows = []
    for entry in entries:
        text = "".join(b.text for b in entry.content if isinstance(b, TextBlock))
        rows.append(MemoryRow(str(entry.memory_id), entry.namespace, entry.key, text[:60], entry.tags))
    return tuple(rows)


def new_entry(namespace: str, key: str, text: str, tags: tuple[str, ...],
              memory_id: MemoryId | None = None) -> MemoryEntry:
    now = datetime.now(timezone.utc)
    return MemoryEntry(memory_id or MemoryId(uuid.uuid4().hex), namespace.strip(), key.strip(),
                       (TextBlock(text),), now, now, tags)
```

**Screen layout** (`memory.py`, id `memory-screen`): namespace `Input`, text `Input`, tags `Input`, `Search` button, `New` button, result list (row buttons `mem-{id}`), detail pane (key/namespace/tags/content) with `Edit`/`Delete` buttons; an edit form modal (`MemoryFormModal`) with namespace/key/text/tags fields (prefilled for edit; namespace+key immutable once created — edits re-`put` with the same id and keep namespace/key).

**Behavioral rules**: Search → `kernel.memory.search(build_query(...))` → rows; empty namespace → inline notice (kernel returns INVALID_ARGUMENT); New → form modal → `new_entry` → `put`; Edit → `put` with same `memory_id`; Delete → `kernel.memory.delete`; after each mutation re-run the current search.

**TDD tests** — model unit ×2 (`test_memory_model.py`): `build_query` tag parsing (commas, whitespace, dedupe not needed); `new_entry` sets id/namespace/key/text/tags and non-null timestamps. Pilot ×8 (`test_memory_screen.py`): search by namespace returns rows; search by tag; view shows detail; create persists (re-search finds it); edit updates content; delete removes it; empty namespace → inline notice; results refresh after a create.

**Verification** — `pytest frontends/tui/tests -q` → **176** · kernel **316**.

---

## Task 5 — Extensions page: Built-in Tools / Skills / MCP tabs, trust/revoke/reload/connect/refresh, typed invocation with approval surfacing

**Files**
- `frontends/tui/kairo_tui/extensions_model.py` (new, pure)
- `frontends/tui/kairo_tui/structs.py` (extend — skill + MCP Protocols)
- `frontends/tui/kairo_tui/screens/extensions.py` (full body)
- `frontends/tui/kairo_tui/screens/inspector.py` (no change — Activity already renders pending interactions; the page only switches the tab)
- tests: `frontends/tui/tests/test_extensions_model.py` (new), `frontends/tui/tests/test_extensions_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.tools.list()/reload()` (→ `KernelResult[tuple[ToolDescriptor, ...]]` — public contract), `kernel.skills.inspect()` (→ `SkillInventory` direct), `active()`, `reload()`, `trust(expected_digest)`, `revoke()` (→ `KernelResult`), `kernel.mcp.catalog()` (synchronous → `tuple[CatalogEntry, ...]`, may raise), `connect()/refresh()` (→ `KernelResult[tuple[McpCatalog, ...]]`), `call_tool(qualified_name, arguments)/read_resource(qualified_name)/render_prompt(qualified_name, arguments)`; `JsonObject`/`freeze_json` for argument parsing; `AuthorizationMode.AUTO` for the success-path test.

**Interfaces — Produces** (`structs.py` additions)

```python
class SkillManifestLike(Protocol):
    name: str
    description: str
    entrypoint: str
    permissions: tuple[str, ...]


class SkillPackageLike(Protocol):
    relative_path: str
    manifest: SkillManifestLike
    manifest_digest: str


class SkillInventoryLike(Protocol):
    digest: str
    status: str
    packages: tuple[SkillPackageLike, ...]


class CatalogEntryLike(Protocol):
    namespace: str
    local_name: str
    qualified_name: str
    raw: dict[str, object]


class McpCatalogLike(Protocol):
    server_name: str
    tools: tuple[CatalogEntryLike, ...]
    resources: tuple[CatalogEntryLike, ...]
    prompts: tuple[CatalogEntryLike, ...]
```

```python
# extensions_model.py
def tool_rows(descriptors: tuple[ToolDescriptor, ...]) -> tuple[str, ...]:
    return tuple(f"{d.name} — {d.description}" for d in descriptors)


def skill_rows(inventory: SkillInventoryLike) -> tuple[str, ...]:
    return tuple(
        f"{p.manifest.name} ({p.relative_path}) — {p.manifest.description}"
        for p in inventory.packages
    )


def mcp_entries(catalog: tuple[object, ...]) -> tuple[CatalogEntryLike, ...]:
    return tuple(cast(CatalogEntryLike, entry) for entry in catalog)


def mcp_groups(entries: tuple[CatalogEntryLike, ...]) -> dict[str, tuple[str, ...]]:
    """namespace → sorted qualified names."""
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(entry.namespace, []).append(entry.qualified_name)
    return {ns: tuple(sorted(names)) for ns, names in groups.items()}
```

**Screen layout** (`extensions.py`, id `extensions-screen`): `TabbedContent` with three panes. **Tools pane**: rows from `kernel.tools.list()` + `Reload` button (`kernel.tools.reload()`). **Skills pane**: inventory status line (`trusted/untrusted/changed/absent` + `digest[:12]` + package count), package rows, `Trust` (passes `inventory.digest`), `Revoke`, `Reload` buttons. **MCP pane**: server/namespace/qualified-name rows from `kernel.mcp.catalog()` (guarded `except Exception` → banner), `Connect`/`Refresh`, an `Invoke` button per tool/prompt row → `McpInvokeModal` (Input for JSON arguments, `freeze_json(json.loads(text or "{}"))`) → `kernel.mcp.call_tool/read_resource/render_prompt(qualified_name, arguments)`; the JSON result is shown in a result pane.

**Approval surfacing** (tui_plan: no auto-approval, pending interactions also visible in the Activity tab):
- A `POLICY_DENIED` result → error banner with the facade's message ("…mode did not authorize external scope; MCP operation rejected.").
- While the broker's request is pending, `fold_event` already adds it to `store.pending_interactions` → the Inspector **Activity tab already renders it** with respond buttons (`act-{id}-{approve|reject|enable}`, `screens/inspector.py:97-118`). The Extensions page shows a "Approval pending — review in Activity" line with a button that activates the tab:

```python
async def _review_in_activity(self) -> None:
    inspector = self._app.query_one("#inspector")
    tabbed = inspector.query_one(TabbedContent)
    tabbed.active = "activity"
```

- The page itself never calls `interactions.respond` on expiry and never auto-approves.

**TDD tests** — model unit ×3 (`test_extensions_model.py`): `tool_rows` formatting; `skill_rows` formatting from a fake inventory object; `mcp_groups` grouping. Pilot ×11 (`test_extensions_screen.py`): tools tab lists `FakeToolRegistry` descriptors; tools reload; skills tab shows status/`absent` for a workspace without `.kairo/skills`; skills trust (fixture writes `.kairo/skills/x/skill.json` → status `changed` → Trust → `trusted`, digest assert); skills revoke → `untrusted`; MCP tab empty state ("No MCP servers.") with an empty hub; connect/refresh return without crashing (empty client list); typed invocation with `POLICY_DENIED` (MANUAL mode) → error banner; the pending interaction appears in `#activity` with an `act-…-approve` button; "review in activity" switches `TabbedContent.active` to `"activity"`; invocation succeeds under `AuthorizationMode.AUTO` (patch preferences first) and the JSON result renders.

**Verification** — `pytest frontends/tui/tests -q` → **190** · kernel **316**.

---

## Task 6 — Settings page: profiles CRUD + role routing + keyring secrets + preferences + document (theme/keybindings/recent workspace)

**Files**
- `frontends/tui/kairo_tui/settings_model.py` (new, pure)
- `frontends/tui/kairo_tui/screens/settings.py` (full body)
- `frontends/tui/kairo_tui/page.py` (extend — `record_recent_workspace`)
- tests: `frontends/tui/tests/test_settings_model.py` (new), `frontends/tui/tests/test_settings_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.providers.snapshot()/create_profile(profile, rev)/update_profile(profile, rev)/delete_profile(profile_id, rev)/map_role(role, profile_id, rev)/unmap_role(role, rev)/probe(profile_id)`, `store_secret(SecretInput)/delete_secret(SecretRef)`, `kernel.preferences.snapshot()/patch(PreferencesPatch(... incl. clear_profile_id))`, `kernel.configuration.snapshot()` (read-only, redacted), `app._bootstrap.secret_store` (`SecretStore.describe/store/delete` + `.available`), `ConfigDocumentAdapter(path, safe_mode).save(document)` + `ConfigAction` dispatch, `ProviderProfile(...)`, `ProfileId`, `SecretId`, `SecretInput`, `ProviderRoleMapping`-like rows from `snapshot().roles` (structural — `ProviderCatalogSnapshot` is a service DTO; access `.revision/.profiles/.roles` via `getattr` or a `structs.py` Protocol).

**Interfaces — Produces**

```python
# settings_model.py
def provider_rows(snapshot) -> tuple[str, ...]:   # snapshot accessed structurally
    return tuple(f"{p.label} ({p.profile_id}) — {p.provider}/{p.model}" for p in snapshot.profiles)


def role_rows(snapshot) -> tuple[str, ...]:
    return tuple(f"{m.role} → {m.profile_id}" for m in snapshot.roles)
```

`record_recent_workspace(app, root)` — prepend `root` (dedup, cap 5) to `document.recent_workspaces`, save via `ConfigDocumentAdapter`, dispatch `ConfigAction`; called from the Workspace page move and the `/workspace` command path.

**Screen layout** (`settings.py`, id `settings-screen`) — sections in a `VerticalScroll`:
1. **Profiles**: list + `New` (form modal: label/provider/model/base_url/context_window/max_output_tokens/temperature) + `Edit` (prefilled) + `Delete` (surfacing CONFLICT when still role-mapped) + `Probe`.
2. **Role routing**: role `Input` + profile `Select` + `Map`/`Unmap`.
3. **Keyring secrets**: backend status (`store.available`); per-profile `secret_id` row showing `SecretDescriptor.source` + `masked_value` + present; `Store` (password modal → `SecretStore.store`) and `Delete` (`store.delete` + `kernel.providers.delete_secret(SecretRef(id))`). Safe mode: writes refused with the `SecretNotStored` message surfaced.
4. **Authorization / Plan / Thinking / context**: `Select` for authorization (Manual/Auto/Yolo, safe-mode forced Manual), `Switch` toggles for Plan/Thinking, numeric `Input`s for `context_trigger_percent` / `context_target_percent` (validated `1..100` / `1..trigger`) + `Input` for `preserve_recent_turns`; one `Apply` button → `preferences.patch` (with `clear_profile_id=True` when the profile selector is set to "none").
5. **Theme / animation / keybindings**: theme `Select` over `app.available_themes` + "default"; reduced-motion checkbox → document field; keybinding pairs list (`Input` key + command per row, persisted verbatim — runtime `App.bind` application is Task 10).
6. **Workspace configuration**: read-only JSON of `kernel.configuration.snapshot().values` (redacted; no patch — see evidence).
7. **Recent workspace**: document `recent_workspaces` list.

All document writes preserve untouched fields (`dataclasses.replace(document, theme=…, keybindings=…, recent_workspaces=…)`) and dispatch `ConfigAction`.

**TDD tests** — model unit ×1 (`test_settings_model.py`): `provider_rows`/`role_rows` formatting from fake snapshots. Pilot ×13 (`test_settings_screen.py`): profiles list renders seeded profile; create profile → appears in catalog; edit profile → updated; delete profile → removed; probe returns reachable (FakeProvider probe success); role map then unmap; secret store → `describe` shows present with masked value; secret delete → not present; authorization Select patches `preferences`; Plan/Thinking switches patch; context trigger/target `Input`s + `preserve_recent_turns` patch (assert snapshot values); `clear_profile_id` clears the runtime profile; a move on the Workspace page records the recent workspace in the document.

**Verification** — `pytest frontends/tui/tests -q` → **204** · kernel **316**.

---

## Task 7 — Doctor page: local/full, per-check status, retry, cancel, copy-redacted report

**Files**
- `frontends/tui/kairo_tui/redaction.py` (new, pure)
- `frontends/tui/kairo_tui/doctor_model.py` (new, pure)
- `frontends/tui/kairo_tui/screens/doctor.py` (full body)
- tests: `frontends/tui/tests/test_redaction.py` (new), `frontends/tui/tests/test_doctor_screen.py` (new)

**Interfaces — Consumes** (all real): `kernel.diagnostics.local()/full()` (→ `KernelResult[DiagnosticReport]`, structural: `.mode/.checks/.duration_ms/.status`, checks `.name/.category/.status/.message/.duration_ms/.details`), `app.copy_to_clipboard(text)`, `app._bootstrap.secret_store`, `document.profiles[*].secret_id`, env vars `KAIRO_SECRET_*`.

**Interfaces — Produces**

```python
# redaction.py
def secret_markers(store: SecretStore, secret_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve present secret values (keyring + KAIRO_SECRET_* env) to mask.

    Values flow into memory only to be replaced; the returned markers are
    never persisted or logged.
    """
    markers: list[str] = []
    for secret_id in secret_ids:
        value = store.resolve(SecretId(secret_id))
        if value and len(value) >= 4:
            markers.append(value)
    for name, value in os.environ.items():
        if name.startswith(ENV_PREFIX) and value and len(value) >= 4:
            markers.append(value)
    return tuple(sorted(set(markers)))


def redact_text(text: str, markers: tuple[str, ...]) -> str:
    redacted = text
    for marker in markers:
        redacted = redacted.replace(marker, "********")
    return redacted
```

```python
# doctor_model.py
def report_to_text(report: DiagnosticReportLike) -> str:
    lines = [f"Kairo diagnostics ({report.mode}) — {report.status}", ""]
    for check in report.checks:
        lines.append(
            f"[{check.status}] {check.name} ({check.category}) — {check.message}"
            f" ({check.duration_ms:.0f} ms)"
        )
        for key, value in check.details:
            lines.append(f"    {key}: {value}")
    lines.append("")
    lines.append(f"Total: {report.duration_ms:.0f} ms")
    return "\n".join(lines)
```

**Screen layout** (`doctor.py`, id `doctor-screen`): `Run local` / `Run full` buttons, a per-check row list (`[status] name — message — ms`, styled by status via CSS classes), a `Retry` button (re-runs the last scope), `Cancel` (cancels the running worker), `Copy redacted report` (builds text → `redact_text(report_to_text(report), secret_markers(store, ids))` → `app.copy_to_clipboard(...)` → notify "Redacted report copied."). Runs execute in a `run_worker` whose handle is kept so Cancel calls `worker.cancel()` (the kernel has no per-diagnostic cancel; probes time out at 5 s each — a UI-level cancel stops the wait and re-renders idle).

**TDD tests** — unit ×4 (`test_redaction.py` + `test_doctor_screen.py` model half): `redact_text` masks a marker everywhere; `redact_text` leaves ordinary text untouched; `report_to_text` renders mode/status/check lines; `secret_markers` returns env values (monkeypatched `os.environ`). Pilot ×6 (`test_doctor_screen.py`): local run renders the check rows with per-check status; full run renders (FakeProvider probe ok, MCP none); retry re-runs the same scope; cancel stops a running run; copy invokes `copy_to_clipboard` with the marker absent (store a marker secret via a memory backend, run diagnostics, click copy, assert the clipboard text never contains the marker); an empty-report state renders.

**Verification** — `pytest frontends/tui/tests -q` → **214** · kernel **316**.

---

## Task 8 — Unified command registry completion: merged catalog, active-session execution, palette

**Files**
- `frontends/tui/kairo_tui/commands.py` (extend — `build_command_palette`)
- `frontends/tui/kairo_tui/app.py` (`_run_command` passes the active session id; `action_command_palette` uses the merged palette)
- `frontends/tui/kairo_tui/screens/commands.py` (render the merged palette; execute kernel commands too)
- tests: `frontends/tui/tests/test_commands.py` (extend), `frontends/tui/tests/test_command_palette.py` (new)

**Interfaces — Consumes**: `kernel.commands.catalog()` (→ `tuple[KernelCommand, ...]` — public contract), `kernel.commands.execute(parsed, session_id)`, `store.state.active_session_id`.

**Steps**

1. `build_command_palette(app)` — merge `TUI_COMMANDS` with the kernel catalog (name → summary); TUI entries win on name clash (`/sessions`, `/workspace`, `/memory`, `/doctor` are TUI nav). Palette buttons get id `cmd-{name-escaped}`; on select: TUI command → `execute_tui_command`; kernel command → `kernel.commands.execute(parsed, session_id=active)`.

2. Fix `_run_command` to pass the active session (currently `session_id=None`, which fails every `needs_session` command):

```python
async def _run_command(self, text: str) -> None:
    from kairo_kernel.contracts.identifiers import SessionId
    from kairo_tui.commands import execute_tui_command, parse_tui_command

    parsed = parse_tui_command(text)
    if parsed is not None and await execute_tui_command(self, parsed):
        return
    parsed_kernel = self.kernel.commands.parse(text)
    if parsed_kernel.ok and parsed_kernel.value is not None:
        session_id = self.store.state.active_session_id
        await self.kernel.commands.execute(
            parsed_kernel.value,
            session_id=SessionId(session_id) if session_id else None,
        )
```

3. Palette header also lists kernel commands with their `help` text; selecting dismisses after execution (existing `CommandPaletteScreen._execute` pattern, extended).

**TDD tests** — unit ×2: `build_command_palette` merges TUI + kernel names with TUI precedence; a helper `active_session_contract(state)` returns `SessionId` or `None`. Pilot ×6: `/new` via the composer creates a session and switches to Chat; `/clear` with an active session clears it (asserts the session history is empty); the palette lists a kernel command (`/status`); selecting `/status` in the palette executes it without error; `/workspace <path>` moves the workspace (kernel command, not nav); `/memory ns text` runs the kernel search and reports.

**Verification** — `pytest frontends/tui/tests -q` → **222** · kernel **316**.

---

## Task 9 — Multimedia ContentBlock metadata cards (no auto-open; explicit save/open)

**Files**
- `frontends/tui/kairo_tui/chat_model.py` (extend — `MediaItem`, `session_timeline` emits media items)
- `frontends/tui/kairo_tui/screens/chat.py` (extend — `MediaCard` widget, `_widget_type`/`_key_for`/`_make_widget`/`_update_widget` cases, `media_save`/`media_open` handlers)
- tests: `frontends/tui/tests/test_chat_model.py` (extend), `frontends/tui/tests/test_chat_screen.py` (extend)

**Interfaces — Consumes**: `ImageBlock(media_type, uri, base64_data, alt_text)`, `AudioBlock(media_type, uri, base64_data, transcript)`, `FileBlock(name, media_type, uri, size_bytes, sha256)`, `ResourceBlock(resource_id, uri, name, description, media_type)` (all `contracts/content.py`).

**Interfaces — Produces**

```python
@dataclass(frozen=True)
class MediaItem:
    message_id: str
    index: int
    kind: str                      # "image" | "audio" | "file" | "resource"
    media_type: str
    name: str                      # alt_text / transcript / name / description
    uri: str
    size_bytes: int | None
    sha256: str
```

`session_timeline` gains a per-message block counter; for each media block it appends `(message.sequence, MediaItem(...))`. The chat screen renders `MediaCard` (border box): `[image] name (image/png, 1234 B)` + `Save` / `Open` buttons (ids `media-{message_id}-{index}-save|open`). **Nothing auto-opens on render.** Save: for `ImageBlock`/`AudioBlock` with `base64_data` → decode and write `<workspace>/kairo_media/{message_id}-{index}-{name-or-unsafe}`; with `uri` (file path) → copy the file; `FileBlock` → copy `uri`; `ResourceBlock` → no payload, notify "Resource is not locally saveable." Open: calls the seam `open_media(path)` (default `os.startfile`/`xdg-open`, monkeypatched in tests) only after the user presses Open, and only when a local file exists. Save path must stay inside the workspace (reject `..`).

**TDD tests** — unit ×3 (`test_chat_model.py`): image/audio/file/resource blocks each produce a `MediaItem` with the right kind/metadata; media items carry a per-message `index`; a mixed message (text + image) yields text and media items in order. Pilot ×3 (`test_chat_screen.py`): a message with an `ImageBlock` renders a media card showing `media_type` and size and the seam `open_media` is **not** called; `Save` writes the base64 bytes to `kairo_media/`; `Open` after save calls the seam with the saved path.

**Verification** — `pytest frontends/tui/tests -q` → **228** · kernel **316**.

---

## Task 10 — Carry-forward fixes: `_key_for` collision, theme application, setup theme wipe, Inspector Changes tab

**Files**
- `frontends/tui/kairo_tui/chat_model.py` (`ReasoningItem.index`), `screens/chat.py` (`_key_for`), `app.py` (`_apply_theme`, reduced-motion class), `screens/setup.py` (`_persist` preserves document fields), `screens/inspector.py` (Changes tab render)
- tests: `frontends/tui/tests/test_chat_model.py`, `test_chat_screen.py`, `test_setup_screen.py`, `test_inspector.py`, `test_toggles.py` (extend)

**Steps**

1. **`_key_for` collision** — add `index: int = 0` to `ReasoningItem`; `session_timeline` increments the index per reasoning block within a message; key:

```python
case ReasoningItem():
    return f"{item.message_id}#{item.index}"
```

2. **Theme application** (`app.py`):

```python
THEME_ALIASES = {"default": "textual-dark"}


def _apply_theme(self) -> None:
    name = self.store.state.document.theme or "default"
    target = self.THEME_ALIASES.get(name, name)
    if target not in self.available_themes:
        target = "textual-dark"
        self.notify(f"Theme '{name}' is not available; using textual-dark.")
    if self.theme != target:
        self.theme = target
```

Called from `on_mount` and from `_on_store_changed` when `state.document.theme` changes. Reduced motion: `self.set_class(self.store.state.reduced_motion, "bp-reduced-motion")` in `on_mount`, with CSS `.bp-reduced-motion #workbench * { animation: none !important; transition: none !important; }` added to `KairoTuiApp.CSS`.

3. **Setup theme wipe** — `_persist` becomes:

```python
from dataclasses import replace
...
document = replace(
    self.store.state.document,
    profiles=profiles,
    roles=(RoleMapping("chat", profile_id),),
    default_profile_id=profile_id,
)
```

4. **Inspector Changes tab** — `_render_changes` lists `WORKSPACE_CHANGED` events newest-first from `state.events` (payload `revision`/`summary` + `ChangeEvent`), plus the current `workspace_revision`; wired into the existing `_on_store`/1 s tick.

**TDD tests** — ×8: model unit `ReasoningItem` carries a per-message index; Pilot: a message with two reasoning blocks renders **two** Collapsibles (the collision regression); Pilot: choosing a theme in Settings applies `app.theme` (and `"default"` maps to `"textual-dark"`); Pilot: `--theme nord` bootstrap applies at startup; unit: `_persist`-style document rebuild preserves `theme`/`keybindings`/`recent_workspaces`; Pilot: completing setup keeps `--theme nord` in the document; Pilot: the Changes tab shows the revision of a dispatched `WORKSPACE_CHANGED` event; Pilot: reduced-motion bootstrap sets the `bp-reduced-motion` class.

**Verification** — `pytest frontends/tui/tests -q` → **236** · kernel **316**.

---

## Task 11 — Final gate verification

No new tests (T11 +0).

**Steps**

1. `pytest frontends/tui/tests -q` → **236 passed**.
2. `pytest tests/kernel -q` → **316 passed**.
3. `ruff check frontends/tui` → clean.
4. `mypy frontends/tui` → clean (whole tree, strict-ish config in `frontends/tui/pyproject.toml`).
5. `python -m pytest frontends/tui/tests/test_boundaries.py -q` → passes (AST boundary holds — no `kairo_kernel.mcp|skills|services|memory|providers` imports leaked).
6. `python -m pytest frontends/tui/tests/test_secret_scan.py -q` → passes (extended coverage: doctor copy text asserted marker-free in Task 7).
7. `python -m pytest tests/kernel -q --co` → 316 (kernel untouched — confirm no kernel file changed: `git status` shows only `frontends/tui/**` and this plan).

**Verification** — all green; final counts **236** TUI + **316** kernel.

---

## Escalated ambiguities

1. **`kernel.configuration.patch` is AST-unusable.** `ConfigPatch`/`ConfigChange` live in `kairo_kernel.services.configuration` (forbidden import) and API 1.1 is frozen. Decision: the Settings page exposes the per-workspace SQLite config **read-only** (redacted snapshot). If raw per-workspace config editing from the TUI is ever required, a future kernel gate must add a public patch contract to `kairo_kernel.contracts`.
2. **MCP typed invocation approval is out-of-turn by design.** `_Mcp._authorize` creates a synthetic interaction identity (`kernel.py:947-964`) with a random `TurnId`/`SessionId`. The pending request surfaces in `store.pending_interactions` and the Inspector Activity tab (already implemented), and the Extensions page adds a "review in Activity" affordance. Approval from the Activity tab is the supported flow; the synthetic session id never matches a real session, so the timeline's trailing interaction card does not appear — only the Activity tab does. Acceptable per tui_plan ("pending interaction 同时出现在消息时间线和 Activity Inspector" — for in-turn requests; out-of-turn MCP approval is Activity-only).
3. **`kernel.mcp.catalog()` can raise** (`McpProtocolError`) and is synchronous; the facade does not wrap it. The TUI guards with `except Exception` → error banner (no kernel fix this gate).
4. **Skill/MCP/workspace/diagnostics DTO types are structurally typed** via `kairo_tui/structs.py` Protocols + `cast` (never imported). Risk: a future kernel DTO rename silently breaks the structural access — mitigated by the per-page Pilot tests asserting real values.
5. **`/sessions` dual meaning resolved as navigation** (the page shows the same list the kernel command prints). The kernel `/sessions` command remains reachable via the palette? No — palette dedupes to the TUI nav entry; the kernel list is the page. Business-command purists may prefer the text output; not worth a second palette entry.
6. **Export and media-save are TUI-side file writes** (the kernel returns payload strings / base64). Paths are confined under the workspace (`kairo_exports/`, `kairo_media/`); this matches "导出" as a page capability but is not a kernel write — flagged for the security review.
7. **Doctor "cancel" is UI-level only** — the kernel has no per-diagnostic cancellation; `Cancel` cancels the awaiting worker (probes still time out server-side at 5 s each). Acceptable for the page capability ("取消" = stop waiting).

## Final gate commands

```
cd C:/Users/Admin/Desktop/project/pyTUI
python -m pytest frontends/tui/tests -q          # 236 passed
python -m pytest tests/kernel -q                 # 316 passed
python -m ruff check frontends/tui               # clean
python -m mypy frontends/tui                     # clean
python -m pytest frontends/tui/tests/test_boundaries.py -q   # AST boundary holds
python -m pytest frontends/tui/tests/test_secret_scan.py -q  # no full key material
```
