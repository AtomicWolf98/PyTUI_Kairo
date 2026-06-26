# Kairo

Kairo is a terminal-native AI coding agent with an animated Textual TUI, plain terminal fallback, persisted sessions, workspace review, context management, OpenAI-compatible model profiles, runtime configuration panels, and local config-first key management.

Kairo 是一个终端原生 AI coding agent，提供 Textual 全屏 TUI、plain 兼容模式、会话持久化、workspace 审查、上下文管理、OpenAI-compatible 模型配置、运行时配置面板，以及本地配置优先的 key 管理。

Current version / 当前版本：**0.2.7-beta**

## Documentation / 文档

- 中文完整手册：[docs/zh/user-manual.md](docs/zh/user-manual.md)
- English manual: [docs/en/user-manual.md](docs/en/user-manual.md)
- Documentation index / 文档入口：[docs/index.md](docs/index.md)

## Highlights / 核心能力

- Animated Textual TUI with Kai mascot, reduced-motion mode, and plain terminal fallback.
- Slash command palette reduced to 18 workflow-oriented commands in 0.2.7-beta.
- `/settings` manages providers, models, API keys, model roles, config validation, backup, restore, import, and export.
- `/sessions` manages persisted conversations, including switch, search, rename, delete, export, and reveal path.
- `/workspace [path-or-bookmark]` opens workspace review or hot-switches the active workspace without restarting.
- `/mode` replaces separate mode commands and controls authorization, Plan Mode, and Thinking Mode.
- `/status` shows a read-only runtime summary with masked key status.
- Strict OpenAI-compatible message packing keeps provider payloads to a single leading `system` message.
- Esc stops the current Textual generation cooperatively; plain mode still uses `Ctrl+C`.

- Textual 动态 TUI、Kai 吉祥物、低动态模式和 plain 终端 fallback。
- 0.2.7-beta 将 slash 命令收敛为 18 条工作流入口。
- `/settings` 管理 provider、model、API key、模型角色、配置校验、备份、恢复、导入和导出。
- `/sessions` 管理持久化会话，包括切换、搜索、重命名、删除、导出和显示路径。
- `/workspace [path-or-bookmark]` 打开 workspace 审查，或在不重启的情况下热切换当前 workspace。
- `/mode` 替代分散的模式命令，统一控制授权级别、Plan Mode 和 Thinking Mode。
- `/status` 显示只读运行状态，并只展示脱敏 key 状态。
- 严格 OpenAI-compatible 消息打包保证 provider payload 只有首位 `system` 消息。
- Textual 模式下 `Esc` 可协作停止当前输出；plain 模式仍使用 `Ctrl+C`。

## Quick Start / 快速开始

### Windows

```powershell
.\run.bat
```

Manual setup / 手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
kairo
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
kairo
```

## First Configuration / 首次配置

```powershell
Copy-Item config.example.json config.json
kairo
```

Inside Kairo, run `/setup` for the first-run wizard or `/settings` for the full configuration panel. Inline API keys may be saved in local `config.json`; Kairo masks keys in UI, logs, session history, doctor, and default exports.

启动 Kairo 后，可运行 `/setup` 使用首次配置向导，或运行 `/settings` 打开完整配置面板。inline API key 可以保存到本地 `config.json`；Kairo 会在 UI、日志、会话历史、doctor 和默认导出中掩码显示 key。

## Common Commands / 常用命令

| Command | Purpose |
| --- | --- |
| `/help` | Show help / 显示帮助 |
| `/model` | Switch chat profile / 切换 chat profile |
| `/setup` | Run first-time setup / 运行首次配置向导 |
| `/settings` | Manage providers, models, keys, roles and config / 管理模型与配置 |
| `/mode` | Change authorization, Plan Mode and Thinking Mode / 切换授权与模式 |
| `/status` | Show runtime status / 显示运行状态 |
| `/new [name]` | Create persisted session / 创建持久化会话 |
| `/sessions` | Manage sessions / 管理会话 |
| `/find <keyword>` | Search sessions / 搜索会话 |
| `/export` | Export session or config / 导出会话或配置 |
| `/compress` | Compress older context / 压缩早期上下文 |
| `/workspace [path-or-bookmark]` | Review or hot-switch workspace / 审查或热切换 workspace |
| `/doctor` | Run health checks / 运行健康检查 |
| `/exit` | Exit / 退出 |

Removed 0.2.7-beta commands such as `/provider add`, `/key set`, `/session export`, `/workspace save`, `/manual`, `/auto`, `/plan`, and `/think` now show migration hints instead of executing. Use `/settings`, `/sessions`, `/workspace`, and `/mode` instead.

0.2.7-beta 已删除 `/provider add`、`/key set`、`/session export`、`/workspace save`、`/manual`、`/auto`、`/plan`、`/think` 等细粒度命令；输入时只显示迁移提示，不再执行。请改用 `/settings`、`/sessions`、`/workspace` 和 `/mode`。

## Privacy / 隐私提醒

Session files may contain prompts, code, file contents, command output, and secrets. The default `.kairo/` directory and local `config.json` should stay out of version control.

Session 文件可能包含提示词、代码、文件内容、命令输出和敏感信息。默认 `.kairo/` 目录和本地 `config.json` 不应提交到版本控制。
