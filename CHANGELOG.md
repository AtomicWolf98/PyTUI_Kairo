# Changelog

本项目采用语义化版本思路记录用户可见变化。发布日期在正式发布流程建立后补充。

## [Unreleased]

## [0.2.1]

### Added

- 三级授权体系：`manual` / `auto` / `yolo`。
- `/workspace move <path>` 命令，支持运行时切换 workspace。
- 工具操作分类（`INTERNAL` / `EXTERNAL` / `SYSTEM` / `DESTRUCTIVE`）与授权确认弹窗。
- `--authorization` 启动参数和 `Ctrl+A` 快捷键循环切换授权级别。
- `--tui` 启动参数强制进入 Textual 界面。
- `workspace_root`、`authorization_level` 与 `policy` 配置字段。
- 完整的用户指南、命令参考、配置说明和故障排查文档。
- 可通过上下键选择、Enter/Tab 补全并滚动访问全部条目的 Slash Command 菜单。
- 右侧 Workspace 文件树、会话触达追踪、Git/非 Git 只读 Diff 与窄屏审查弹窗。
- 分段着色的上下文进度条、响应式 Workspace Dock 和 Kai 状态动画。
- `llm.providers` 多供应商/模型配置结构、上下文管理和进程内多会话。
- LLM SSE transport 的代理支持、错误分类和瞬时错误重试。
- Composer 多行输入：`Shift+Enter` / `Ctrl+Enter` 插入换行，并按视觉行数自动增高。
- 长文本与 Markdown 表格折行显示，代码和 Diff 保留可滚动访问能力。

### Changed

- `auto_mode` 布尔字段迁移为 `authorization_level` 字符串字段，旧配置自动兼容。
- 系统提示词明确 Kairo 身份与三种授权级别含义。
- 宽屏 Workspace Dock 改为约三分之一的响应式宽度，并限制在 36 至 64 列。
- 配置示例与保存格式统一到 `llm.providers`、`policy` 和新 UI 字段。

### Fixed

- 修复 Textual 主线程执行 `/config` 等同步命令时错误调用 `call_from_thread()` 导致应用崩溃的问题。
- 修复 `/manual`、`/yolo` 与 `/workspace move <path>` 在全屏 TUI 中行为不一致的问题。
- 修复 workspace 切换后右侧 Tree 仍显示旧目录的问题。
- 修复快速连续切换 workspace 时旧扫描结果覆盖新 Dock 的问题。
- 修复 Markdown 表格超长单元格被静默裁剪的问题。
- 修复 Composer 只能显示一行以及 soft-wrap 后不增高的问题。
- 修复 Windows Terminal 中 Shift/Ctrl+Enter 被识别为普通 Enter 并直接提交的问题。

### Security

- 移除仓库工作树中的明文 provider 凭据。
- 阻止环境变量注入的 API Key 被配置保存操作写回磁盘。
- provider 在 `api_key_env` 未命中时回退到本地 `api_key`，避免切换后运行时密钥被意外清空。
- 文件路径、网络、Shell、Python、skill hash 和资源限制进入统一 policy 配置。

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
