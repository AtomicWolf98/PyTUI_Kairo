# Commands / 命令

Current version / 当前版本：**0.2.6-beta**

The complete command reference is maintained in the bilingual manuals:

完整命令说明维护在双语手册中：

- 中文：[Slash 命令](zh/user-manual.md#4-slash-命令)
- English: [Slash Commands](en/user-manual.md#4-slash-commands)

## Notes / 说明 (0.2.6-beta)

- `/model` switches the **chat profile** in a single transaction. If `model_roles.chat` is configured it is updated too, so the next chat request uses the selected profile. `/roles` is still used for `plan`/`compress`/`fast` routing.
- Editing a provider no longer clears other providers' inline API keys. Leave the key blank to keep the existing key; use the explicit clear option to clear only the target.
- All LLM request payloads are folded to a single leading `system` message (`llm.strict_message_packing`, default `true`).
- Textual mode: press `Esc` while streaming or running tools to stop the current output. Plain mode: `Ctrl+C`.

- `/model` 以单一事务切换 **chat profile**。若配置了 `model_roles.chat` 会同步更新，下一次 chat 请求使用新 profile。`/roles` 仍用于 `plan`/`compress`/`fast` 路由。
- 编辑某个 provider 不会清空其它 provider 的 inline API key。留空保留原 key；使用显式 clear 只清空目标。
- 所有 LLM 请求 payload 折叠为唯一首位 `system` 消息（`llm.strict_message_packing`，默认 `true`）。
- Textual 模式：流式输出/工具运行中按 `Esc` 停止当前输出；plain 模式：`Ctrl+C`。

## Command List

| Command | Purpose |
| --- | --- |
| `/help` | Show help |
| `/exit` | Exit Kairo |
| `/config` | Show current settings (now includes API key safety hint) |
| `/config validate` | Validate current configuration and list issues |
| `/config backup` | Write a timestamped backup of `config.json` |
| `/config restore` | Restore a previously written backup |
| `/settings` | Open the settings menu (plain + TUI) |
| `/providers` | List configured providers |
| `/provider add` | Add a new provider via wizard |
| `/provider edit` | Edit an existing provider |
| `/provider remove` | Remove a provider (with confirmation) |
| `/provider test` | Test provider connection / key / model |
| `/model` | Select a configured model profile |
| `/model add` | Add a model to a provider |
| `/model edit` | Edit an existing model |
| `/model remove` | Remove a model (with confirmation) |
| `/model test` | Test a model against its provider |
| `/manual` | Confirm every tool call |
| `/auto` | Auto-run normal in-workspace tools |
| `/yolo` | Skip tool confirmations |
| `/plan` | Toggle Plan Mode |
| `/think` | Toggle Thinking Mode |
| `/skills` | List tools and skills |
| `/new [name]` | Create and switch to a new persisted session |
| `/sessions` | Switch saved sessions |
| `/session rename` | Rename the current session |
| `/session delete` | Delete a session (with confirmation) |
| `/session export` | Export the current session as Markdown or JSON |
| `/session reveal` | Print the current session file path |
| `/clear` | Clear the current session |
| `/undo` | Undo the latest conversation turn |
| `/compress` | Compress older context |
| `/docs` | List local documentation paths |
| `/docs config` | Open the configuration doc path |
| `/docs providers` | Open the provider doc path |
| `/docs sessions` | Open the sessions doc path |
| `/workspace` | Show the current workspace |
| `/workspace move <path>` | Hot-switch workspace without restarting |
