# Changelog / 更新记录

## [0.2.6-beta]

### Added / 新增

- Unified model switch transaction: `/model` now switches the chat profile through a single closed transaction (`Config.switch_active_profile` / `Agent.switch_model_profile`) that keeps `llm.active_profile`, `model_roles.chat`, `ConversationManager` runtime state, context window and all sessions consistent. The next chat request is guaranteed to use the selected profile.
- Strict OpenAI-compatible message packing layer (`agent/message_packer.py`): all LLM request payloads are folded into a single leading `system` message (main prompt + `kairo_runtime_state` + `[Conversation Summary]`). Controlled by `llm.strict_message_packing` (default `true`). Internal history is left untouched; only the provider payload is affected.
- Cooperative `Esc` stop generation in the Textual UI (`agent/cancellation.py` `CancellationToken`): pressing `Esc` while streaming or running tools stops the current output, saves the partial assistant response with a `[stopped]` marker, and restores composer focus. Plain mode still uses `Ctrl+C`. Controlled by `ui.esc_stops_generation` (default `true`) and `ui.stop_saves_partial_response` (default `true`).
- Provider edit key semantics: blank API key input now keeps the existing key; an explicit "Clear stored API key" option (Textual modal) / `empty` mode (plain) only clears the target provider's key.
- `ConversationManager.validate_provider_payload()` validates a packed payload against the strict OpenAI message ordering.
- Defensive backstop in `LLMClient.stream_response`: refuses to send a payload with a `system` message after the leading slot when strict packing is enabled.
- Session load warning when an old session contains a `system` message after the first user message (folded at request time, persisted history unchanged).

### Fixed / 修复

- `/model` no longer fails to switch when `model_roles.chat` is configured: the chat role is updated alongside `active_profile` so UI display and the actual request profile match.
- Profile resolver legacy fallback: `llm.active_profile == ""` no longer masks the legacy `active_provider`/`active_model` selection; the resolver now falls through correctly for `llm.providers[]` configs.
- Editing one provider/profile no longer clears other providers' inline API keys: the deprecated `allowed_inline_providers` global strip was removed from `ConfigDraft.apply_to`. Existing inline keys are always preserved; `allow_inline_key=False` now refuses only *new* inline keys.
- `ConfigDraft.clear_key()` now uses an explicit `KEY_CLEAR` sentinel instead of an empty string (which now means "keep existing").

### Changed / 变更

- `pyproject.toml` version bumped to `0.2.6`. Brand header, welcome panel and dock now show `v0.2.6`.
- `config.example.json` now documents `llm.strict_message_packing`, `ui.esc_stops_generation` and `ui.stop_saves_partial_response`.
- `LLMClient.stream_response` accepts an optional `cancel_token` and yields `("stopped", None)` when cancelled.
- `InteractionRunner` accepts `cancel_token` on `run_interaction` / `run_interaction_events` / `compress_context` and checks it before each LLM round and after each tool.

### Tests / 测试

- `tests/test_model_switching.py`: profile-mode and legacy-mode `/model` switching, `model_roles.chat` override, plan/compress role isolation, context window update, runtime state sync, request payload uses the new profile.
- `tests/test_provider_key_preservation.py`: editing one provider preserves other inline keys, blank keeps existing, explicit clear only clears target, replace only replaces target, save+reload preserves keys (provider and profile structures).
- `tests/test_message_packer.py`: system folding, summary/runtime fold, no system after index 0, tool pairing preserved, internal fields stripped, non-strict pass-through, runner integration.
- `tests/test_stop_generation.py`: `CancellationToken`, Esc cancels streaming and saves partial, stop during tool prevents the next LLM round, history remains valid, next message works after stop.

- 统一模型切换事务：`/model` 通过单一闭环事务（`Config.switch_active_profile` / `Agent.switch_model_profile`）切换 chat profile，保持 `llm.active_profile`、`model_roles.chat`、`ConversationManager` 运行时状态、context window 与所有 session 一致，下一次 chat 请求必定使用新 profile。
- 严格 OpenAI-compatible 消息打包层（`agent/message_packer.py`）：所有 LLM 请求 payload 折叠为唯一首位 `system` 消息（主提示词 + `kairo_runtime_state` + `[Conversation Summary]`）。由 `llm.strict_message_packing`（默认 `true`）控制。内部 history 不变，仅影响 provider payload。
- Textual 协作 `Esc` 停止输出（`agent/cancellation.py` `CancellationToken`）：流式输出/工具运行中按 `Esc` 停止当前输出，partial 回复以 `[stopped]` 标记保存并恢复 composer focus。plain 模式仍使用 `Ctrl+C`。由 `ui.esc_stops_generation`（默认 `true`）与 `ui.stop_saves_partial_response`（默认 `true`）控制。
- Provider 编辑 key 语义：API key 留空保留原 key；显式 "Clear stored API key"（Textual modal）/ `empty` 模式（plain）只清空目标 provider 的 key。
- `ConversationManager.validate_provider_payload()` 校验打包后的 payload 是否满足严格 OpenAI 消息顺序。
- `LLMClient.stream_response` 防御性校验：strict 模式下发现首位之后存在 `system` 消息时拒绝发送。
- 加载旧 session 时，若首位 user 之后存在 `system` 消息则记录 warning（请求时折叠，持久化 history 不变）。

- 修复 `/model` 在 `model_roles.chat` 已配置时切换无效：chat role 与 `active_profile` 同步更新，UI 显示与实际请求 profile 一致。
- 修复 profile resolver legacy 回退：`llm.active_profile == ""` 不再遮蔽 legacy `active_provider`/`active_model` 选择。
- 修复编辑某个 provider 误清空其它 provider inline key：移除 `ConfigDraft.apply_to` 中已弃用的 `allowed_inline_providers` 全局剥离逻辑，始终保留既有 inline key；`allow_inline_key=False` 仅拒绝本次新输入的 key。
- `ConfigDraft.clear_key()` 改用显式 `KEY_CLEAR` 哨兵，空字符串现在表示 "保留原 key"。

- `pyproject.toml` 版本号升级至 `0.2.6`，品牌头、欢迎面板与 dock 显示 `v0.2.6`。
- `config.example.json` 新增 `llm.strict_message_packing`、`ui.esc_stops_generation`、`ui.stop_saves_partial_response` 示例。
- `LLMClient.stream_response` 接受可选 `cancel_token`，取消时 yield `("stopped", None)`。
- `InteractionRunner` 的 `run_interaction` / `run_interaction_events` / `compress_context` 接受 `cancel_token`，在每轮 LLM 前和每个工具后检查。

## [0.2.5-beta]

### Added / 新增

- Config-first model profiles (`llm.profiles[]`): each profile is an independent runtime unit with its own `base_url`, `api_key`, `model`, temperature, max tokens and context window.
- Profile resolver (`agent/profile_resolver.py`): unifies `llm.profiles[]` and legacy `llm.providers[]` into a single `ResolvedProfile` runtime view.
- Local config API key management: `/keys`, `/key set <profile>`, `/key clear <profile>`, `/key reveal <profile>`, `/key migrate`.
  - Inline API keys are persisted in `config.json` by default (0.2.5 plaintext key policy).
  - All UI output masks keys unless the user explicitly confirms reveal/export with keys.
- Model roles (`model_roles`): `/roles`, `/role set <role> <profile>`, `/role clear <role>`; supported roles are `chat`, `plan`, `compress`, `fast`.
- Workspace bookmarks: `/workspace save <name>`, `/workspaces`, `/workspace move <name-or-path>`, `/workspace remove <name>`.
- Session search: `/session search <keyword>` and `/session open <id-or-index>` search session names and history content read-only.
- Config import/export: `/config export`, `/config export --with-keys`, `/config import <path>`; export defaults to `.kairo/config_exports/config.export.YYYYMMDD-HHMMSS.json` with redacted keys.
- Doctor health dashboard: `/doctor` checks config parse, duplicate profile ids, active profile, key presence, base URL scheme, workspace/session writability, git availability and provider reachability without leaking keys.
- TUI modals for profile editor, key editor (password input), role editor, confirmation, search results and doctor dashboard.
- `ConfigDraft` extended with profile/key/role/bookmark management and redacted config export.

- 配置优先的模型 profile（`llm.profiles[]`）：每个 profile 是独立的运行单元，包含独立的 `base_url`、`api_key`、`model`、temperature、max tokens 与 context window。
- Profile 解析层（`agent/profile_resolver.py`）：将 `llm.profiles[]` 与旧版 `llm.providers[]` 统一解析为 `ResolvedProfile` 运行时视图。
- 本地配置 API Key 管理：`/keys`、`/key set <profile>`、`/key clear <profile>`、`/key reveal <profile>`、`/key migrate`。
  - 默认将 inline API Key 明文写入 `config.json`（0.2.5 明文 key 策略）。
  - 所有 UI 输出默认 mask key；仅在用户二次确认后才 reveal 或导出完整 key。
- 模型角色（`model_roles`）：`/roles`、`/role set <role> <profile>`、`/role clear <role>`；支持 `chat`、`plan`、`compress`、`fast`。
- Workspace 书签：`/workspace save <name>`、`/workspaces`、`/workspace move <name-or-path>`、`/workspace remove <name>`。
- 会话搜索：`/session search <keyword>` 与 `/session open <id-or-index>` 只读搜索会话名称与历史内容。
- 配置导入/导出：`/config export`、`/config export --with-keys`、`/config import <path>`；默认导出到 `.kairo/config_exports/config.export.YYYYMMDD-HHMMSS.json` 并脱敏。
- Doctor 健康面板：`/doctor` 检查配置解析、profile id 重复、active profile、key 缺失、URL 协议、workspace/session 可写性、git 可用性与 provider 可达性，且不泄漏 key。
- TUI modal：profile 编辑器、key 编辑器（密码输入）、role 编辑器、确认框、搜索结果与 doctor 面板。
- `ConfigDraft` 扩展：支持 profile/key/role/bookmark 管理与脱敏配置导出。

### Changed / 变更

- `pyproject.toml` version bumped to `0.2.5`.
- `config.example.json` now uses the new `llm.profiles[]` structure with empty `api_key` strings and optional `api_key_env` examples.
- `/config` output is now profile-first and includes model roles and workspace bookmarks.
- `/model` switches `llm.active_profile` when profiles are configured.
- `LLMClient.stream_response()` supports `profile_role` and `profile_id` to route chat/plan/compress requests through different profiles.

- `pyproject.toml` 版本号升级至 `0.2.5`。
- `config.example.json` 已更新为新的 `llm.profiles[]` 结构，`api_key` 为空字符串，`api_key_env` 仅作兼容示例。
- `/config` 输出改为 profile 优先，并展示 model roles 与 workspace bookmarks。
- `/model` 在 profile 模式下切换 `llm.active_profile`。
- `LLMClient.stream_response()` 支持 `profile_role` 与 `profile_id`，可将 chat/plan/compress 请求路由到不同 profile。

### Tests / 测试

- `tests/test_0_2_5_features.py`: profile resolver, config profiles, ConfigDraft profile/key/role/bookmark operations, runtime command handlers, dispatcher routing, key masking, doctor non-leakage, config export redaction.

- `tests/test_0_2_5_features.py`：profile 解析器、config profiles、ConfigDraft 的 profile/key/role/bookmark 操作、运行时命令处理器、dispatcher 路由、key 掩码、doctor 防泄漏、配置导出脱敏。

## [0.2.4]

### Fixed / 修复

- **P0 SecretConfirmModal import**: added missing import in `app.py` so `/provider add|edit` inline API key confirmation no longer raises `NameError`.
- **P0 Textual/plain boundary**: `/session rename|delete|export`, `/config validate|backup|restore`, `/docs` are now routed to dedicated Textual modals or UI notices instead of calling plain `input()`/`print()` inside the TUI.
- **P0 Worker thread safety**: `_run_plain_to_view` now uses `call_from_thread()` for all UI updates from worker threads.
- **P1 Workspace move history invariant**: `move_workspace()` no longer appends a trailing system message to history; the notice is delivered via `CommandResult.message` and UI events only.
- **P1 `/undo` persistence**: undo now calls `replace_active_history(..., save=True)` so the change is persisted immediately to disk.
- **P1 Session config fields**: `autosave` flag is now wired through to `ConversationManager.mark_dirty()`; dirty tracking persists when autosave is enabled.
- **P2 `Any` import**: added missing `Any` to `typing` imports in `widgets.py`.
- **P2 `SessionStore.create_session()`**: now accepts optional `history` and `context_window` parameters; docstring clarifies it is a low-level helper.
- **P2 History invariant validator**: added `ConversationManager.validate_history_invariants()` to check system-prefix, no system after first user, no orphan tool results.
- **P2 `delete_session` docstring**: corrected misleading description.
- **P2 Plain mode stability**: degraded `input_framed_with_dock` to a stable single-line `kairo >` prompt; removed dynamic dropdown, dock widget and Unicode box-drawing characters that caused redraw glitches on Windows terminals.
- **P3 ruff F-class cleanup**: resolved all 33 `ruff --select F` errors (F401 unused imports, F821 undefined names, F841 unused variables) across 17 source and test files.

- **P0 SecretConfirmModal 导入修复**：在 `app.py` 中补入缺失的导入，`/provider add|edit` 的 inline API key 确认不再抛 `NameError`。
- **P0 Textual/plain 边界收口**：`/session rename|delete|export`、`/config validate|backup|restore`、`/docs` 现在路由到专用 Textual modal 或 UI notice，不再在 TUI 内调用 plain `input()`/`print()`。
- **P0 Worker 线程安全**：`_run_plain_to_view` 现在使用 `call_from_thread()` 进行所有 UI 更新。
- **P1 Workspace move 历史不变量**：`move_workspace()` 不再向 history 尾部追加 system 消息；通知仅通过 `CommandResult.message` 和 UI 事件传达。
- **P1 `/undo` 持久化**：undo 现在调用 `replace_active_history(..., save=True)`，变更立即写入磁盘。
- **P1 Session 配置字段**：`autosave` 标志已连接到 `ConversationManager.mark_dirty()`；启用 autosave 时脏数据会自动落盘。
- **P2 `Any` 导入修复**：在 `widgets.py` 的 `typing` 导入中补入缺失的 `Any`。
- **P2 `SessionStore.create_session()`**：现接受可选 `history` 和 `context_window` 参数；docstring 明确标注为低层级 helper。
- **P2 History 不变量校验器**：新增 `ConversationManager.validate_history_invariants()`，检查 system 前置、首个 user 后无 system、无孤立 tool result。
- **P2 `delete_session` docstring**：修正了误导性描述。
- **P2 Plain 模式稳定性**：将 `input_framed_with_dock` 降级为稳定的单行 `kairo >` 提示符；移除了动态下拉、dock 组件和 Unicode 框线字符，解决了 Windows 终端的重绘乱码问题。
- **P3 ruff F 类清理**：跨 17 个源文件和测试文件修复了全部 33 个 `ruff --select F` 错误。

## [0.2.3]

### Added / 新增

- Runtime Configuration Center: add/edit/remove providers and models without restarting Kairo.
  - `/providers`, `/provider add|edit|remove|test`, `/model add|edit|remove|test`, `/settings`.
  - Picker list and editor modals available in the Textual TUI; full plain-mode prompt chain parity.
- Provider health check (`agent/provider_health.py`): minimal OpenAI-compatible probe classifies results into Success / Auth / Model / URL / Rate Limit / Unknown; tests are skipped in session history and context compression.
- `ConfigDraft` (`agent/config_editor.py`): in-memory editable copy of the live config, structural validator, atomic write with auto-backup and rollback-on-failure.
  - Graceful guarantees: writes never persist env-provided API keys; inline keys require explicit confirmation before being saved to disk.
- Config backups and restore: `/config validate`, `/config backup`, `/config restore` with timestamped `config.backup.YYYYMMDD-HHMMSS.json` files.
- Session management extensions: `/session rename`, `/session delete`, `/session export` (Markdown or JSON, written to `<storage_dir>/exports/`), `/session reveal` (prints the on-disk path).
- Built-in provider templates powering the first-run wizard (`agent/provider_templates.py`): OpenAI, DeepSeek, MiniMax, Moonshot/Kimi, Qwen, OpenRouter, Local OpenAI-compatible, Custom. Templates store no API keys.
- First-run wizard: plain mode runs an interactive prompt chain when `llm.providers` is empty or the active model is invalid; Textual mode shows a guidance notice pointing at `/provider add`.
- API Key safety hint in `/config`: shows `env(VAIRO_…_API_KEY) present|missing`, `inline in config.json [warning] …abcd`, or `missing`; never prints full keys.

- 运行时配置中心：在不退出 Kairo 的情况下增删改 provider 与 model。
  - 新增 `/providers`、`/provider add|edit|remove|test`、`/model add|edit|remove|test`、`/settings`。
  - TUI 内提供选择列表与编辑 Modal；plain 模式提供同步问答链路，两套功能等价。
- Provider 健康检查（`agent/provider_health.py`）：用最小 OpenAI-compatible 探测调用，结果分级为 Success / Auth / Model / URL / Rate Limit / Unknown；测试请求不会写入会话历史、不会触发 context compression。
- `ConfigDraft`（`agent/config_editor.py`）：对运行配置做内存拷贝，支持结构校验、原子写入、保存前自动备份、保存失败自动回滚。
  - 安全保证：env 注入的 API Key 永不落盘；inline key 必须二次确认才会写入 `config.json`。
- 配置备份与恢复：`/config validate`、`/config backup`、`/config restore`；备份命名为带时间戳的 `config.backup.YYYYMMDD-HHMMSS.json`。
- 会话管理扩展：`/session rename`、`/session delete`、`/session export`（导出 Markdown 或 JSON 到 `<storage_dir>/exports/`）、`/session reveal`（打印会话文件的绝对路径）。
- Provider 内置模板驱动首次启动向导（`agent/provider_templates.py`）：OpenAI、DeepSeek、MiniMax、Moonshot/Kimi、Qwen、OpenRouter、Local OpenAI-compatible、Custom；模板不包含任何 API Key。
- 首次启动向导：`llm.providers` 为空或 active model 无效时，plain 模式启动交互式 prompt；TUI 模式显示提示，引导用户输入 `/provider add`。
- `/config` 输出新增 API Key 安全提示：`env(KAIRO_…_API_KEY) present|missing`、`inline in config.json [warning] …abcd` 或 `missing`；不会显示完整密钥。

### Changed / 变更

- `pyproject.toml` version bumped to `0.2.3`. Brand header and welcome panel now show `v0.2.3`.
- Slash command catalog expanded with runtime config and session commands; the dispatcher routes two-word commands (`/provider add`, `/model test`, `/config validate`, etc.) before falling back to single-word handlers.
- `/config` now ends with a hint pointing users at `/provider add`, `/model add`, and `/settings` for runtime editing.

- `pyproject.toml` 版本号升级至 `0.2.3`；品牌头与欢迎面板显示 `v0.2.3`。
- 斜杠命令清单新增运行时配置与会话命令；dispatcher 在匹配单字命令之前先匹配两段式命令（`/provider add`、`/model test`、`/config validate` 等）。
- `/config` 输出末尾追加编辑提示，引导使用 `/provider add`、`/model add`、`/settings`。

### Tests / 测试

- `tests/test_config_editor.py`: ConfigDraft validation, add/update/remove provider/model, env-key isolation, backup/restore round trip, save-failure rollback.
- `tests/test_provider_health.py`: classification for 200/401/403/404/400+model-marker/429/URLError/invalid scheme/unknown 4xx; payload enforced without tools or streaming.
- `tests/test_runtime_config_commands.py`: plain-mode flows for `/providers`, `/provider add` (wizard), `/model add|remove`, `/config validate|backup|restore`, `/docs`.
- `tests/test_settings_ui.py`: Textual headless tests for `SettingsScreen`, `ProviderListModal`, `ProviderEditorModal`, `ModelEditorModal`, `ConnectionTestModal`.
- `tests/test_session_management_commands.py`: rename/delete/export/reveal flows with on-disk file and index consistency.
- `tests/test_provider_templates.py`: required templates, default dict shape, no embedded keys, env name presence.

- `tests/test_config_editor.py`：ConfigDraft 校验/增改删、env key 隔离、备份恢复、保存失败回滚。
- `tests/test_provider_health.py`：200/401/403/404/400+model标记/429/URLError/非法Scheme/未知4xx 多类错误分类；payload 始终不带 tools 且非 streaming。
- `tests/test_runtime_config_commands.py`：plain 模式 `/providers`、`/provider add` 向导、`/model add|remove`、`/config validate|backup|restore`、`/docs` 等流程。
- `tests/test_settings_ui.py`：Textual headless 测试覆盖 `SettingsScreen`、`ProviderListModal`、`ProviderEditorModal`、`ModelEditorModal`、`ConnectionTestModal`。
- `tests/test_session_management_commands.py`：rename/delete/export/reveal 与 on-disk 一致性。
- `tests/test_provider_templates.py`：模板齐全性、默认 dict 结构、不嵌入 key、env 名称齐备。

## [0.2.2]

### Added / 新增

- Persisted sessions with independent JSON files, `index.json`, and active-session restore.
- `sessions` config block: `enabled`, `storage_dir`, `autosave`, `save_interval_seconds`, `max_sessions`.
- Runtime state message for workspace/model/authorization so the next model request sees current state.
- Workspace hot-switch behavior for tool roots, shell cwd, Python REPL reset, custom skill reload, Dock refresh, and active conversation state.
- Bilingual user manuals under `docs/zh/` and `docs/en/`.
- Tests for session storage and workspace hot switching.

- 会话持久化：独立 JSON 文件、`index.json` 和最后活动会话恢复。
- 新增 `sessions` 配置块：`enabled`、`storage_dir`、`autosave`、`save_interval_seconds`、`max_sessions`。
- 新增 runtime state system message，让模型请求能看到当前 workspace、model 和授权状态。
- Workspace 热切换覆盖工具 root、Shell cwd、Python REPL reset、自定义 skill reload、Dock 刷新和当前会话状态。
- 新增 `docs/zh/` 与 `docs/en/` 双语用户手册。
- 新增 session store 与 workspace hot-switch 测试。

### Changed / 变更

- `/new` and `/sessions` now create and switch persisted sessions when session storage is enabled.
- `/workspace move <path>` is now a current-process state transition rather than a setting that requires restart.
- Documentation now uses bilingual manuals as the source of truth for user-facing usage.

- `/new` 和 `/sessions` 在启用 session storage 时创建和切换持久化会话。
- `/workspace move <path>` 现在是当前进程立即生效的状态切换，不需要重启。
- 用户文档改为以双语完整手册为主入口。

### Fixed / 修复

- Search result paths are relative to the active workspace root after workspace moves.
- Model requests no longer rely on stale conversation text to infer the current workspace.
- Runtime help text now describes persisted sessions instead of in-memory sessions.

- workspace move 后搜索结果路径以当前 workspace root 为基准。
- 模型请求不再依赖旧对话文本推断当前 workspace。
- 运行时帮助文字已改为说明持久化会话，而不是进程内会话。

## [0.2.1]

### Added / 新增

- Authorization levels: `manual`, `auto`, `yolo`.
- `/workspace move <path>` command.
- Scrollable Slash Command palette.
- Workspace Dock with file tree, touched files, Git/non-Git diff review, and narrow-screen modal.
- Responsive Dock width, context progress bar, Kai state animation, multiline composer, and wide-content rendering.
- Provider/model profile configuration under `llm.providers`.
- Context management and process-local multi-session support.

- 三档授权：`manual`、`auto`、`yolo`。
- `/workspace move <path>` 命令。
- 可滚动 Slash Command 菜单。
- Workspace Dock：文件树、会话触达文件、Git/非 Git Diff 和窄屏弹窗。
- 响应式 Dock 宽度、上下文进度条、Kai 状态动画、多行输入和宽文本渲染。
- `llm.providers` provider/model profile 配置。
- 上下文管理和进程内多会话。

### Fixed / 修复

- `/config` no longer crashes the Textual UI.
- Fast workspace switches no longer let stale scans overwrite current Dock state.
- Workspace tree refreshes when switching between directories with identical structures.
- Windows modifier handling for `Shift+Enter` and `Ctrl+Enter`.

- 修复 `/config` 在 Textual UI 中崩溃。
- 修复快速 workspace 切换时旧扫描覆盖新 Dock 状态。
- 修复相同文件结构 workspace 切换时 Tree 不刷新。
- 修复 Windows 下 `Shift+Enter` 和 `Ctrl+Enter` 修饰键处理。

## [0.2.0]

### Added / 新增

- Kairo brand, Kai terminal mascot, Textual full-screen TUI, animation, responsive Dock, and plain fallback.
- Model profiles, context management, and process-local multi-session support.
- `/compress`, `/new`, and `/sessions`.

- Kairo 品牌、Kai 终端吉祥物、Textual 全屏 TUI、动画、响应式 Dock 和 plain fallback。
- 模型 profile、上下文管理和进程内多会话。
- `/compress`、`/new` 和 `/sessions`。

## [0.1.0]

### Added / 新增

- pyTUI prototype with Rich CLI, OpenAI-compatible streaming client, and basic local tools.

- pyTUI 原型，包含 Rich CLI、OpenAI-compatible 流式客户端和基础本地工具。
