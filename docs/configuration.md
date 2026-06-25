# Configuration / 配置

The complete configuration guide is maintained in the bilingual manuals:

完整配置说明维护在双语手册中：

- 中文：[首次配置](zh/user-manual.md#2-首次配置)、[会话持久化](zh/user-manual.md#7-会话持久化)、[运行时配置](zh/user-manual.md#8-运行时配置)
- English: [First Configuration](en/user-manual.md#2-first-configuration), [Persisted Sessions](en/user-manual.md#7-persisted-sessions), [Runtime Configuration](en/user-manual.md#8-runtime-configuration)

## Important Blocks

### `llm`

Defines OpenAI-compatible profiles. Since 0.2.5 Kairo uses `llm.profiles[]` as the primary format; legacy `llm.providers[]` configs are still loaded and converted automatically. Since 0.2.6 `llm.strict_message_packing` (default `true`) folds every LLM request payload into a single leading `system` message for strict OpenAI-compatible providers. You can manage profiles, keys, roles, bookmarks, and config import/export from inside Kairo without editing `config.json`:

- `/providers` · `/provider add` · `/provider edit` · `/provider remove` · `/provider test`
- `/model add` · `/model edit` · `/model remove` · `/model test`
- `/keys` · `/key set` · `/key clear` · `/key reveal` · `/key migrate`
- `/roles` · `/role set` · `/role clear`
- `/workspaces` · `/workspace save` · `/workspace remove`
- `/config validate` · `/config backup` · `/config restore` · `/config export` · `/config import`
- `/settings` (TUI menu)

Use `/model` to switch the chat profile. Since 0.2.6 this is a single transaction that keeps `model_roles.chat` consistent. Use `/role set chat <profile>` to route different tasks to different profiles. Editing one provider never clears another provider's inline key; leave the key blank to keep it.

定义 OpenAI-compatible 模型 profile。0.2.5 起 Kairo 以 `llm.profiles[]` 为主格式；旧版 `llm.providers[]` 仍会被自动转换。0.2.6 起 `llm.strict_message_packing`（默认 `true`）将每个 LLM 请求 payload 折叠为唯一首位 `system` 消息，兼容严格 OpenAI-compatible provider。可以在 Kairo 内管理 profile、key、role、书签和配置导入导出，无需手动编辑 `config.json`：

- `/providers`、`/provider add|edit|remove|test`
- `/model add|edit|remove|test`
- `/keys`、`/key set|clear|reveal|migrate`
- `/roles`、`/role set|clear`
- `/workspaces`、`/workspace save|remove`
- `/config validate|backup|restore|export|import`
- `/settings`（TUI 菜单）

使用 `/model` 切换 chat profile。0.2.6 起这是单一事务，会保持 `model_roles.chat` 一致。使用 `/role set chat <profile>` 把不同任务路由到不同 profile。编辑某个 provider 不会清空其它 provider 的 inline key；留空即保留原 key。

### API Key Safety

- **Local deployment default**: inline `api_key` in `config.json` is allowed, but the file contains secrets and must not be committed.
- Prefer `api_key_env` so keys stay in environment variables when multiple users or CI share the project.
- `/key reveal` and `/config export --with-keys` require explicit confirmation.
- `/config`, logs, session history, and `/doctor` show only masked previews; full keys are never printed.

- **本地部署默认**：允许在 `config.json` 中保存 inline `api_key`，但该文件包含密钥，不可提交到仓库。
- 若多人或 CI 共用项目，仍推荐 `api_key_env`，让 key 保存在环境变量中。
- `/key reveal` 和 `/config export --with-keys` 需要二次确认。
- `/config`、日志、会话历史、`/doctor` 只显示掩码预览，不会输出完整 key。

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

Controls TUI mode, animations, Dock width, workspace scan interval, and diff limits. Since 0.2.6 it also exposes `esc_stops_generation` (default `true`, lets `Esc` stop the current generation in Textual mode) and `stop_saves_partial_response` (default `true`, saves the partial assistant reply with a `[stopped]` marker when stopped).

控制 TUI 模式、动画、Dock 宽度、workspace 扫描频率和 Diff 限制。0.2.6 起新增 `esc_stops_generation`（默认 `true`，Textual 模式下 `Esc` 停止当前输出）与 `stop_saves_partial_response`（默认 `true`，停止时以 `[stopped]` 标记保存 partial 回复）。

### `workspace_root`

The active workspace root. It can also be changed at runtime with `/workspace move <path>` or `/workspace move <bookmark-name>`.

当前 workspace 根目录，也可以运行时用 `/workspace move <path>` 或 `/workspace move <bookmark-name>` 切换。

### `policy`

Controls path, network, command, Python, skill, and resource-limit safety rules.

控制路径、网络、命令、Python、skill 和资源限制策略。
