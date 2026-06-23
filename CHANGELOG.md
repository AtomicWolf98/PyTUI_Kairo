# Changelog / 更新记录

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
