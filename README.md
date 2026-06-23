# Kairo

Kairo is a terminal-native AI coding agent with an animated Textual TUI, plain terminal fallback, persisted sessions, workspace review, context management, OpenAI-compatible model profiles, and runtime provider/model configuration.

Kairo 是一个终端原生 AI coding agent，提供 Textual 全屏 TUI、plain 兼容模式、会话持久化、workspace 审查、上下文管理、OpenAI-compatible 模型配置，并支持在 TUI 内运行时配置 provider 与 model。

Current version / 当前版本：**0.2.3**

## Documentation / 文档

- 中文完整手册：[docs/zh/user-manual.md](docs/zh/user-manual.md)
- English manual: [docs/en/user-manual.md](docs/en/user-manual.md)
- Documentation index / 文档入口：[docs/index.md](docs/index.md)

## Highlights / 核心能力

- Animated TUI with Kai mascot and reduced-motion/plain fallbacks.
- Slash command palette with keyboard selection and completion.
- Configured model profiles through `llm.providers`; switch with `/model`.
- **Runtime configuration** (0.2.3): add/edit/remove providers and models with `/providers`, `/provider add|edit|remove|test`, `/model add|edit|remove|test`, `/settings`, and validate/backup/restore with `/config validate|backup|restore`.
- **Provider health check** (0.2.3): `/provider test` and `/model test` verify reachability, key validity, and model acceptance.
- **API key safety** (0.2.3): env keys are never persisted back to `config.json`; inline keys require explicit confirmation; `/config` shows only safe previews.
- Persisted sessions under `sessions.storage_dir`; switch with `/sessions`, rename/delete/export/reveal with `/session ...`.
- Manual and automatic context compression with `/compress`.
- Workspace Dock with file tree, touched files, Git/non-Git diff review, and context progress.
- Runtime workspace hot switching with `/workspace move <path>`.
- Built-in file, search, patch, shell, Python, web, and custom skill tools.
- Authorization levels: `manual`, `auto`, and `yolo`.

- Kai 终端动效与低动效/plain fallback。
- 支持键盘选择和补全的 Slash 命令菜单。
- 通过 `llm.providers` 配置模型 profile，并用 `/model` 切换。
- **运行时配置**（0.2.3）：使用 `/providers`、`/provider add|edit|remove|test`、`/model add|edit|remove|test`、`/settings` 以及 `/config validate|backup|restore` 增删改 provider/model 和备份恢复配置。
- **Provider 健康检查**（0.2.3）：`/provider test`、`/model test` 检测连通性、key 有效性和模型名。
- **API Key 安全**（0.2.3）：env key 不落盘；inline key 保存前需确认；`/config` 只显示安全预览。
- 会话持久化到 `sessions.storage_dir`，`/sessions` 切换，`/session rename|delete|export|reveal` 整理。
- 使用 `/compress` 进行手动或自动上下文压缩。
- Workspace Dock 含文件树、变更文件、Git/非 Git diff 预览、上下文进度。
- `/workspace move <path>` 当前进程热切换 workspace。
- 内置文件、搜索、patch、shell、Python、web、自定义 skill 工具。
- 授权级别：`manual`、`auto`、`yolo`。
- `/compress` 手动压缩和自动上下文治理。
- Workspace Dock 显示文件树、会话触达文件、Git/非 Git Diff 和上下文进度。
- `/workspace move <path>` 运行时热切换 workspace。
- 内置文件、搜索、patch、Shell、Python、Web 和自定义 skill 工具。
- 授权级别：`manual`、`auto`、`yolo`。

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
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
kairo
```

Use `api_key_env` whenever possible so secrets stay in environment variables instead of `config.json`.

推荐使用 `api_key_env`，让密钥保留在环境变量中，而不是写入 `config.json`。

## Common Commands / 常用命令

| Command | Purpose |
| --- | --- |
| `/help` | Show help / 显示帮助 |
| `/model` | Select model profile / 选择模型 profile |
| `/new [name]` | Create persisted session / 创建持久化会话 |
| `/sessions` | Switch sessions / 切换会话 |
| `/compress` | Compress older context / 压缩早期上下文 |
| `/workspace move <path>` | Hot-switch workspace / 热切换 workspace |
| `/manual` `/auto` `/yolo` | Change authorization / 切换授权级别 |
| `/plan` `/think` | Toggle modes / 切换模式 |
| `/exit` | Exit / 退出 |

## Privacy / 隐私提醒

Session files may contain prompts, code, file contents, command output, and secrets. The default `.kairo/` directory is ignored by Git.

Session 文件可能包含提示词、代码、文件内容、命令输出和敏感信息。默认 `.kairo/` 目录已被 Git 忽略。
