# Commands / 命令

Current version / 当前版本：**0.2.7-beta**

The complete command reference is maintained in the bilingual manuals:

完整命令说明维护在双语手册中：

- 中文：[Slash 命令](zh/user-manual.md#4-slash-命令)
- English: [Slash Commands](en/user-manual.md#4-slash-commands)

## Notes / 说明 (0.2.7-beta)

- `/model` is **switch-only**: it selects the current chat profile in a single transaction. Provider/model management moved to `/settings`.
- `/settings` opens the config panel for providers, models, keys, roles, and config operations.
- `/sessions` opens the session management panel for switch, search, rename, delete, export, and reveal.
- `/workspace` opens the workspace panel with no argument, or hot-switches to a path/bookmark when an argument is given.
- `/mode` replaces `/manual`, `/auto`, `/yolo`, `/plan`, and `/think` as the unified mode panel.
- `/status` replaces the read-only use of `/config` with a focused runtime status view.
- `/find <keyword>` replaces `/session search` as the unified session search entry.
- `/export` unifies session export and config export with redaction by default.

- `/model` **仅用于切换** chat profile，是单一事务。provider/model 管理已迁移到 `/settings`。
- `/settings` 打开配置面板，管理 providers、models、keys、roles 与配置操作。
- `/sessions` 打开会话管理面板，支持切换、搜索、重命名、删除、导出与显示路径。
- `/workspace` 无参数时打开 workspace 面板，带参数时作为路径或书签名热切换 workspace。
- `/mode` 统一替代 `/manual`、`/auto`、`/yolo`、`/plan`、`/think`。
- `/status` 替代原 `/config` 的只读展示用途，集中显示运行状态。
- `/find <keyword>` 统一替代 `/session search`，作为会话搜索入口。
- `/export` 统一会话导出与配置导出，默认脱敏。

## Command List

| Command | Purpose |
| --- | --- |
| `/help` | Show grouped help |
| `/exit` | Exit Kairo |
| `/new [name]` | Create and switch to a new persisted session |
| `/sessions` | Open the session management panel |
| `/clear` | Clear the current session |
| `/undo` | Undo the latest conversation turn |
| `/compress` | Manually compress older context |
| `/model` | Switch the current chat profile |
| `/setup` | Run the first-run setup wizard |
| `/settings` | Open the settings/config panel |
| `/mode` | Open the mode panel (authorization, plan, thinking) |
| `/workspace [path-or-bookmark]` | Open workspace panel or hot-switch workspace |
| `/status` | Show read-only runtime status |
| `/find <keyword>` | Search current and persisted sessions |
| `/export` | Export session or config |
| `/doctor` | Run health checks |
| `/skills` | List tools and skills |
| `/docs` | Show local documentation index |

## Removed commands / 已删除命令

The following commands were removed in 0.2.7-beta. Inputting them now shows a migration hint instead of running old behavior.

以下命令在 0.2.7-beta 中已删除，现在输入会返回迁移提示，不再执行旧逻辑。

| Removed command / 已删除命令 | Migration / 迁移方式 |
| --- | --- |
| `/manual` `/auto` `/yolo` `/plan` `/think` | Use `/mode` / 使用 `/mode` |
| `/providers` `/provider add|edit|remove|test` | Use `/settings` > Providers / 使用 `/settings` 的 Providers |
| `/model add|edit|remove|test` | Use `/settings` > Models / 使用 `/settings` 的 Models |
| `/keys` `/key set|clear|reveal|migrate` | Use `/settings` > Keys / 使用 `/settings` 的 Keys |
| `/roles` `/role set|clear` | Use `/settings` > Roles / 使用 `/settings` 的 Roles |
| `/config validate|backup|restore|export|import` | Use `/settings` > Config or `/export` / 使用 `/settings` 的 Config 或 `/export` |
| `/session rename|delete|export|reveal|search|open` | Use `/sessions` / 使用 `/sessions` |
| `/workspace save` `/workspaces` `/workspace remove` | Use `/workspace` / 使用 `/workspace` |
| `/docs config` `/docs providers` `/docs sessions` | Use `/docs` / 使用 `/docs` |
