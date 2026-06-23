# Changelog / 更新记录

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
