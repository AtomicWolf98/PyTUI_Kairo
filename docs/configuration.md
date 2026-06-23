# Configuration / 配置

The complete configuration guide is maintained in the bilingual manuals:

完整配置说明维护在双语手册中：

- 中文：[首次配置](zh/user-manual.md#2-首次配置)、[会话持久化](zh/user-manual.md#7-会话持久化)、[运行时配置](zh/user-manual.md#8-运行时配置)
- English: [First Configuration](en/user-manual.md#2-first-configuration), [Persisted Sessions](en/user-manual.md#7-persisted-sessions), [Runtime Configuration](en/user-manual.md#8-runtime-configuration)

## Important Blocks

### `llm`

Defines OpenAI-compatible providers and model profiles. You can now add, edit, and remove providers/models from inside Kairo without editing `config.json`:

- `/providers` · `/provider add` · `/provider edit` · `/provider remove` · `/provider test`
- `/model add` · `/model edit` · `/model remove` · `/model test`
- `/settings` (TUI menu) · `/config validate` · `/config backup` · `/config restore`

Use `/model` to switch among configured profiles.

定义 OpenAI-compatible provider 和模型 profile。现在也可以在 Kairo 界面内直接增删改 provider 和 model，无需手动编辑 `config.json`：

- `/providers`、`/provider add|edit|remove|test`
- `/model add|edit|remove|test`
- `/settings`（TUI 菜单）、`/config validate|backup|restore`

使用 `/model` 在已配置 profile 中切换。

### API Key Safety

- Prefer `api_key_env` so keys stay in environment variables.
- Environment-variable keys are **never** persisted to `config.json`.
- Inline `api_key` requires explicit confirmation before saving.
- `/config` displays only safe previews of the active key source.

- 推荐 `api_key_env`，把 API Key 放在环境变量中。
- 环境变量中的 Key 永远不会被写回 `config.json`。
- inline `api_key` 保存前需要明确确认。
- `/config` 仅显示当前密钥来源的安全预览。

### `sessions`

Controls persisted conversations.

控制会话持久化。

```json
"sessions": {
  "enabled": true,
  "storage_dir": ".kairo/sessions",
  "autosave": true,
  "save_interval_seconds": 1.0,
  "max_sessions": 200
}
```

### `context_management`

Controls automatic compression, trigger threshold, target size, and recent-turn preservation.

控制自动压缩、触发阈值、目标占用和近期轮次保留数量。

### `ui`

Controls TUI mode, animations, Dock width, workspace scan interval, and diff limits.

控制 TUI 模式、动画、Dock 宽度、workspace 扫描频率和 Diff 限制。

### `workspace_root`

The active workspace root. It can also be changed at runtime with `/workspace move <path>`.

当前 workspace 根目录，也可以运行时用 `/workspace move <path>` 切换。

### `policy`

Controls path, network, command, Python, skill, and resource-limit safety rules.

控制路径、网络、命令、Python、skill 和资源限制策略。
