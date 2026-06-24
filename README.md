# Kairo

Kairo is a terminal-native AI coding agent with an animated Textual TUI, plain terminal fallback, persisted sessions, workspace review, context management, OpenAI-compatible model profiles, runtime provider/model configuration, and local config-first key management.

Kairo 是一个终端原生 AI coding agent，提供 Textual 全屏 TUI、plain 兼容模式、会话持久化、workspace 审查、上下文管理、OpenAI-compatible 模型配置、运行时 provider/model 配置，以及本地配置优先的 key 管理。

Current version / 当前版本：**0.2.5-beta**

## Documentation / 文档

- 中文完整手册：[docs/zh/user-manual.md](docs/zh/user-manual.md)
- English manual: [docs/en/user-manual.md](docs/en/user-manual.md)
- Documentation index / 文档入口：[docs/index.md](docs/index.md)

## Highlights / 核心能力

- Animated TUI with Kai mascot and reduced-motion/plain fallbacks.
- Slash command palette with keyboard selection and completion.
- Animated TUI with Kai mascot and reduced-motion/plain fallbacks.
- Slash command palette with keyboard selection and completion.
- Configured model profiles through `llm.profiles[]` (new in 0.2.5) or legacy `llm.providers`; switch with `/model`.
- **Local config-first key management** (0.2.5): `/keys`, `/key set|clear|reveal|migrate` manage inline keys in `config.json` with mask-by-default safety.
- **Model roles** (0.2.5): `/roles`, `/role set|clear` route `chat`, `plan`, `compress`, `fast` tasks to different profiles.
- **Workspace bookmarks** (0.2.5): `/workspace save`, `/workspaces`, `/workspace move <name-or-path>`, `/workspace remove`.
- **Session search** (0.2.5): `/session search <keyword>` finds sessions read-only; `/session open <id-or-index>` switches to the found session.
- **Config import/export** (0.2.5): `/config export`, `/config export --with-keys`, `/config import <path>` with redaction by default.
- **Doctor health dashboard** (0.2.5): `/doctor` checks config, keys, workspace, sessions, git and provider reachability.
- **Runtime configuration** (0.2.3): add/edit/remove providers and models with `/providers`, `/provider add|edit|remove|test`, `/model add|edit|remove|test`, `/settings`, and validate/backup/restore with `/config validate|backup|restore`.
- **Provider health check** (0.2.3): `/provider test` and `/model test` verify reachability, key validity, and model acceptance.
- **API key safety** (0.2.5): inline keys are allowed in `config.json` for local deployment but are masked in UI, logs, session history, doctor and default exports; env keys remain supported; reveal/export-with-keys requires confirmation.
- Persisted sessions under `sessions.storage_dir`; switch with `/sessions`, rename/delete/export/reveal/search/open with `/session ...`.
- Manual and automatic context compression with `/compress`.
- Workspace Dock with file tree, touched files, Git/non-Git diff review, and context progress.
- Runtime workspace hot switching with `/workspace move <path>` or a saved bookmark name.
- Built-in file, search, patch, shell, Python, web, and custom skill tools.
- Authorization levels: `manual`, `auto`, and `yolo`.

- Kai 终端动效与低动效/plain fallback。
- 支持键盘选择和补全的 Slash 命令菜单。
- 通过新版 `llm.profiles[]`（0.2.5）或旧版 `llm.providers` 配置模型 profile，并用 `/model` 切换。
- **本地配置优先的 key 管理**（0.2.5）：`/keys`、`/key set|clear|reveal|migrate` 管理 `config.json` 中的 inline key，默认掩码显示。
- **模型角色**（0.2.5）：`/roles`、`/role set|clear` 将 `chat`、`plan`、`compress`、`fast` 任务路由到不同 profile。
- **Workspace 书签**（0.2.5）：`/workspace save`、`/workspaces`、`/workspace move <name-or-path>`、`/workspace remove`。
- **会话搜索**（0.2.5）：`/session search <keyword>` 与 `/session open <id-or-index>` 只读搜索会话。
- **配置导入/导出**（0.2.5）：`/config export`、`/config export --with-keys`、`/config import <path>`，默认脱敏。
- **Doctor 健康面板**（0.2.5）：`/doctor` 检查配置、key、workspace、session、git 与 provider 连通性。
- **运行时配置**（0.2.3）：使用 `/providers`、`/provider add|edit|remove|test`、`/model add|edit|remove|test`、`/settings` 以及 `/config validate|backup|restore` 增删改 provider/model 和备份恢复配置。
- **Provider 健康检查**（0.2.3）：`/provider test`、`/model test` 检测连通性、key 有效性和模型名。
- **API Key 安全**（0.2.5）：为本地部署允许 `config.json` 保存 inline key，但 UI、日志、会话历史、doctor 与默认导出均掩码显示；仍支持 env key；reveal/导出完整 key 需二次确认。
- 会话持久化到 `sessions.storage_dir`，`/sessions` 切换，`/session rename|delete|export|reveal|search|open` 整理。
- 使用 `/compress` 进行手动或自动上下文压缩。
- Workspace Dock 含文件树、变更文件、Git/非 Git diff 预览、上下文进度。
- `/workspace move <path>` 或书签名称当前进程热切换 workspace。
- 内置文件、搜索、patch、shell、Python、web、自定义 skill 工具。
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
