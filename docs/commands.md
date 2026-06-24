# Commands / 命令索引

Current version / 当前版本：**0.2.5-beta**

The complete command explanations are maintained in the bilingual manuals:

完整命令说明维护在双语用户手册中：

- [English Slash Commands](en/user-manual.md#4-slash-commands)
- [中文 Slash 命令](zh/user-manual.md#4-slash-命令)

## Core / 核心

| Command | Purpose |
| --- | --- |
| `/help` | Show command help |
| `/exit` | Exit Kairo |
| `/plan` | Toggle Plan Mode |
| `/manual` | Confirm every tool call |
| `/auto` | Auto-run normal in-workspace tools |
| `/yolo` | Run tools without confirmation |
| `/think` | Toggle Thinking Mode |
| `/skills` | List tools and skills |
| `/clear` | Clear the active conversation |
| `/undo` | Undo the latest conversation turn |
| `/compress` | Compress older context |

## Sessions / 会话

| Command | Purpose |
| --- | --- |
| `/new [name]` | Create and switch to a new persisted conversation |
| `/sessions` | Switch persisted conversations |
| `/session rename` | Rename the current session |
| `/session delete` | Delete a session with confirmation |
| `/session export` | Export the current session as Markdown or JSON |
| `/session reveal` | Show the current session file path |
| `/session search <keyword>` | Search saved sessions by name and history content |
| `/session open <id-or-index>` | Switch to a session by id or latest search result index |

## Model Profiles And Keys / 模型与密钥

| Command | Purpose |
| --- | --- |
| `/model` | Select the active model profile |
| `/profiles` or `/profile` | List configured profiles |
| `/profile add` | Add a profile |
| `/profile edit` | Edit a profile |
| `/profile remove` | Remove a profile |
| `/keys` | List masked API key status for profiles |
| `/key set <profile>` | Save an inline API key to `config.json` |
| `/key clear <profile>` | Clear a profile inline API key |
| `/key reveal <profile>` | Reveal a full key after confirmation |
| `/key migrate` | Migrate legacy provider inline keys into profile keys |
| `/roles` | List model role mappings |
| `/role set <role> <profile>` | Route `chat`, `plan`, `compress` or `fast` to a profile |
| `/role clear <role>` | Clear a role mapping |

## Legacy Provider Tools / 旧版 Provider 工具

These commands remain for compatibility with `llm.providers[]` configs.

这些命令继续兼容旧版 `llm.providers[]` 配置。

| Command | Purpose |
| --- | --- |
| `/providers` | List configured providers |
| `/provider add` | Add a provider wizard |
| `/provider edit` | Edit provider URL/key/name |
| `/provider remove` | Remove a provider |
| `/provider test` | Test provider reachability |
| `/model add` | Add a model to a provider |
| `/model edit` | Edit a provider model |
| `/model remove` | Remove a provider model |
| `/model test` | Test a model |
| `/settings` | Open the settings menu |

## Config / 配置

| Command | Purpose |
| --- | --- |
| `/config` | Show current configuration with masked key status |
| `/config validate` | Validate current configuration |
| `/config backup` | Create a timestamped backup |
| `/config restore` | Restore a previous backup |
| `/config export [path]` | Export a redacted config copy; API keys are blanked |
| `/config export --with-keys [path]` | Export plaintext keys after confirmation |
| `/config import <path>` | Import and validate a config JSON file |
| `/doctor` | Run health checks; Textual provider probe runs in a worker |

## Workspace / 工作区

| Command | Purpose |
| --- | --- |
| `/workspace` | Show the active workspace |
| `/workspace move <path-or-bookmark>` | Hot-switch workspace without restarting |
| `/workspace save <name>` | Bookmark the current workspace |
| `/workspaces` | List workspace bookmarks |
| `/workspace remove <name>` | Remove a workspace bookmark |

## Docs / 文档

| Command | Purpose |
| --- | --- |
| `/docs` | List local documentation paths |
| `/docs config` | Show the configuration doc path |
| `/docs providers` | Show the provider doc path |
| `/docs sessions` | Show the session doc path |
