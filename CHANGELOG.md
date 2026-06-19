# Changelog

本项目采用语义化版本思路记录用户可见变化。发布日期在正式发布流程建立后补充。

## [Unreleased]

### Added

- 三级授权体系：`manual` / `auto` / `yolo`。
- `/workspace move <path>` 命令，支持运行时切换 workspace。
- 工具操作分类（`INTERNAL` / `EXTERNAL` / `SYSTEM` / `DESTRUCTIVE`）与授权确认弹窗。
- `--authorization` 启动参数和 `Ctrl+A` 快捷键循环切换授权级别。
- `--tui` 启动参数强制进入 Textual 界面。
- `workspace_root`、`authorization_level` 与 `policy` 配置字段。
- `agent/interaction.py` 拆分 LLM 交互循环，`agent/provider_registry.py` 与 `agent/config_migration.py` 拆分配置逻辑。
- 完整用户文档与开发者指南收敛。

### Changed

- `auto_mode` 布尔字段迁移为 `authorization_level` 字符串字段，旧配置自动兼容。
- `harness.py` 移动到 `tests/harness.py`。
- 系统提示词明确 Kairo 身份与三种授权级别含义。

### Fixed

- 修复 Textual 主线程执行 `/config` 等同步命令时错误调用 `call_from_thread()` 导致应用崩溃的问题。

### Added

- 可通过上下键选择、Enter/Tab 补全的 Slash Command 菜单。
- 右侧 Workspace 文件树、会话触达追踪、Git/非 Git 只读 Diff 与窄屏审查弹窗。
- 分段着色的上下文使用进度条和右下角紧凑运行状态。
- 分层文档系统、维护交接说明和安全指南。
- `llm.providers` 单文件 AI 配置结构，以及 provider / model 运行时选择。
- `agent/commands.py` 作为 plain、Textual 与 `/help` 共用的命令元数据源。
- bootstrap 装配回归测试与基础注册测试。

### Changed

- 宽屏 Workspace Dock 改为约三分之一的响应式宽度，并在 36 至 64 列之间约束。
- Slash Command 菜单不再截断为前 7 条，所有命令均可通过键盘或鼠标滚动访问。
- 配置改为 `config.example.json` 模板加本地 `config.json`。
- 工程元数据加入 README、开发依赖和 pytest 默认配置。
- `harness.py` 改为复用 `agent.bootstrap`，不再复制工具注册逻辑。
- `/model` 由旧的平铺 `model_profiles` 选择收敛为 `provider / model` 选择。
- 配置文档与示例统一到 `llm.providers` 语义。

### Security

- 移除仓库工作树中的明文 provider 凭据。
- 阻止环境变量注入的 API Key 被配置保存操作写回磁盘。
- provider 在 `api_key_env` 未命中时回退到本地 `api_key`，避免切换后运行时密钥被意外清空。

## [0.2.0]

### Added

- Kairo 品牌与 Kai 固定单元格动画。
- Textual 全屏 TUI、响应式 Dock 和 plain fallback。
- 模型 profile、进程内多会话和上下文管理。
- `/compress`、`/new`、`/sessions` 等交互命令。
- Agent 事件流、工具输出隔离和 Textual headless 测试。

## [0.1.0]

### Added

- pyTUI 原型、Rich CLI、OpenAI-compatible 流式客户端和基础工具系统。
