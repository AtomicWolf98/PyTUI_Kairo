# Commands / 命令

The complete command reference is maintained in the bilingual manuals:

完整命令说明维护在双语手册中：

- 中文：[Slash 命令](zh/user-manual.md#4-slash-命令)
- English: [Slash Commands](en/user-manual.md#4-slash-commands)

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
