# Kairo

Kairo is a terminal-native AI coding agent. Since 0.4.0a2 it ships as two packages: `kairo-kernel` (frontend-neutral kernel: kernel API 1.1, sessions, workspace review, context management, provider profiles, MCP, and config/change-event streams) and `kairo-tui` (the Textual frontend).

Kairo 是一个终端原生 AI coding agent。自 0.4.0a2 起拆分为两个包：`kairo-kernel`（前端无关内核：kernel API 1.1、会话、workspace 审查、上下文管理、provider 配置、MCP，以及配置/变更事件流）与 `kairo-tui`（Textual 前端）。

Current version / 当前版本：**0.4.0a2**

## Two-Package Layout / 双包结构

`kairo-tui [WORKSPACE]` launches the Textual interface over the `kairo-kernel` public API. Options: `--config PATH` (global config-v1.json), `--theme NAME`, `--reduced-motion`, `--safe-mode`, and `--headless-smoke`. The legacy `kairo --tui` entry jumps to kairo-tui 0.4.0a2 and prints an install hint (`python -m pip install kairo-tui`) when the package is missing. Legacy `config.json` and sessions are not migrated in this phase.

`kairo-tui [WORKSPACE]` 通过 `kairo-kernel` 公共 API 启动 Textual 界面。选项：`--config PATH`（全局 config-v1.json）、`--theme NAME`、`--reduced-motion`、`--safe-mode` 和 `--headless-smoke`。旧入口 `kairo --tui` 跳转到 kairo-tui 0.4.0a2；未安装时打印安装提示（`python -m pip install kairo-tui`）。本阶段不迁移旧版 `config.json` 与会话。

## WebUI Preview / WebUI 预览

Kairo 0.3.3 stabilizes the optional local browser workbench with safer settings, cleaner authentication, stronger event matching and better Workspace layout:

```powershell
kairo --web
kairo --web --port 8765
kairo --web --no-browser
```

The WebUI is local-only by default (`127.0.0.1`). Startup links may include a temporary auth token, but the browser removes it from the visible URL after first load and uses local header/session auth for API calls. It includes a graphical chat timeline, tool approval cards, workspace tree/diff review, sessions management, settings panels, skills and doctor checks. `--tui` and `--plain` remain supported.

Kairo 0.3.3 对可选本地浏览器工作台进行稳定性收口：更安全的 settings 保存、更干净的本地认证、更可靠的事件匹配和更合理的 Workspace 布局。默认只监听 `127.0.0.1`；启动链接可以携带临时 token，但前端首次读取后会从可见 URL 中移除，并通过本地 header/session 认证调用 API。WebUI 包含项目/会话侧栏、聊天流、工具审批卡片、workspace tree/diff、session 管理、全表单 settings、skills 和 doctor 检查。`--tui` 与 `--plain` 继续保留。

## Documentation / 文档

- 中文完整手册：[docs/zh/user-manual.md](docs/zh/user-manual.md)
- English manual: [docs/en/user-manual.md](docs/en/user-manual.md)
- Documentation index / 文档入口：[docs/index.md](docs/index.md)

## Highlights / 核心能力

- Animated Textual TUI (`kairo-tui`) with reduced-motion mode; the legacy `kairo` command keeps the plain terminal fallback.
- Slash command palette reduced to 18 workflow-oriented commands in 0.2.7-beta.
- `/settings` manages providers, models, API keys, model roles, config validation, backup, restore, import, and export.
- `/sessions` manages persisted conversations, including switch, search, rename, delete, export, and reveal path.
- `/workspace [path-or-bookmark]` opens workspace review or hot-switches the active workspace without restarting.
- `/mode` replaces separate mode commands and controls authorization, Plan Mode, and Thinking Mode.
- `/status` shows a read-only runtime summary with masked key status.
- Strict OpenAI-compatible message packing keeps provider payloads to a single leading `system` message.
- Esc stops the current Textual generation cooperatively; plain mode still uses `Ctrl+C`.

- 动态 Textual TUI（`kairo-tui`）与低动态模式；旧版 `kairo` 命令保留 plain 终端 fallback。
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
.\install.bat
kairo-tui
```

`install.bat` installs both the `kairo-kernel` and `kairo-tui` wheels into the owned `%LOCALAPPDATA%\Kairo` environment, creates `%LOCALAPPDATA%\Kairo\bin\kairo.bat` and `kairo-tui.bat`, and puts that folder first in the current user's PATH. The two commands launch the same new TUI. A known stale wrapper from pre-0.4 installations is preserved as `.legacy`; an unknown existing target is rejected. Open a new PowerShell window after installation.

Quick run without installing the user-level command:

```powershell
.\run-tui.bat
```

Manual setup / 手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e . -e frontends/tui
kairo-tui
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . -e frontends/tui
kairo-tui
```

## First Configuration / 首次配置

```powershell
Copy-Item config.example.json config.json
kairo-tui
```

Inside Kairo TUI, run the setup page or `/settings` for the full configuration panel. The new TUI uses the versioned `config-v1.json` document and Keyring/environment references; the old `config.json` and legacy plain/WebUI entry points are not part of this installation.

启动 Kairo TUI 后，可使用 Setup 页面或 `/settings` 打开配置面板。新版 TUI 使用版本化 `config-v1.json` 与 Keyring/环境变量引用；旧版 `config.json` 及 legacy plain/WebUI 入口不属于本安装包。

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
