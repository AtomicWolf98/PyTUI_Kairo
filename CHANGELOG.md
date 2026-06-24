# Changelog / 更新记录

## [0.2.5-beta]

### Added / 新增

- Config-first model profiles: `llm.profiles[]` is now the primary model configuration format. Each profile can define its own `base_url`, `api_key`, `api_key_env`, `model`, `temperature`, `max_tokens`, `context_window` and optional context management overrides.
- Profile resolver: `agent/profile_resolver.py` unifies new profiles and legacy `llm.providers[]` configs into one runtime `ResolvedProfile` view.
- Local API key management: `/keys`, `/key set <profile>`, `/key clear <profile>`, `/key reveal <profile>` and `/key migrate`.
- Model roles: `/roles`, `/role set <role> <profile>`, `/role clear <role>` route `chat`, `plan`, `compress` and `fast` work to different profiles.
- Workspace bookmarks: `/workspace save <name>`, `/workspaces`, `/workspace move <name-or-path>` and `/workspace remove <name>`.
- Session search: `/session search <keyword>` searches saved sessions read-only; `/session open <id-or-index>` switches to a matching session.
- Config import/export: `/config export [path]`, `/config export --with-keys [path]` and `/config import <path>`. Default export blanks API keys; plaintext export requires confirmation.
- Doctor health dashboard: `/doctor` checks config, keys, workspace, sessions, git and provider reachability. In Textual mode, the network provider probe runs in a worker so the UI does not freeze.
- New Textual modals for profile editing, key editing, role editing, confirmations, search results and doctor checks.

### Changed / 变更

- `pyproject.toml` version is now `0.2.5`.
- `config.example.json` uses `llm.profiles[]` with empty `api_key` values and optional `api_key_env` examples.
- `/config` output is profile-first and includes key safety, model roles and workspace bookmarks.
- `/model` switches `llm.active_profile` when profiles are configured.
- `LLMClient.stream_response()` accepts `profile_role` and `profile_id` so chat, plan and compression requests can use separate profiles.
- Config save failures now raise and roll back through `ConfigDraft.apply_to()` instead of reporting false success.
- Import rejects redacted key previews such as `sk...1234` so masked exports cannot become fake API keys.

### Tests / 测试

- Added 0.2.5 coverage for profile resolution, profile persistence, key management, role routing, workspace bookmarks, session search, config import/export, masked-key import refusal, doctor non-leakage and command dispatch.

## [0.2.4]

### Fixed / 修复

- Fixed missing `SecretConfirmModal` import in Textual inline-key flows.
- Closed Textual/plain boundaries for session, config and docs commands so Textual no longer calls plain `input()` flows.
- Routed worker-thread UI updates through the event bridge.
- Preserved message-order invariants during `/workspace move`.
- Persisted `/undo` immediately to session storage.
- Wired session autosave and related configuration.
- Added history invariant validation.
- Stabilized plain mode input by falling back to a simple single-line prompt.
- Cleaned ruff F-class static errors.

## [0.2.3]

### Added / 新增

- Runtime configuration center for providers and models.
- Provider/model add, edit, remove and test commands.
- Textual modals and plain prompt parity for runtime configuration.
- Provider health probe with classified connection results.

## [0.2.2]

### Added / 新增

- Persistent session storage.
- Configurable session storage directory.
- Workspace hot-switching groundwork.

## [0.2.1]

### Added / 新增

- Context management planning and early compression workflows.
- Dock context usage display.

## [0.2.0]

### Added / 新增

- Kairo branding, Textual TUI, Kai mascot and dynamic workspace dock.

## [0.1.0]

### Added / 新增

- Initial pure-Python CLI/TUI agent prototype.
