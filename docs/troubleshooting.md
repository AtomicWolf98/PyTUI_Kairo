# Troubleshooting / 故障排查

The maintained troubleshooting sections are in the bilingual manuals:

最新排障说明维护在双语手册中：

- 中文：[常见问题](zh/user-manual.md#13-常见问题)
- English: [Troubleshooting](en/user-manual.md#13-troubleshooting)

## Quick Checks / 快速检查

| Problem | Check |
| --- | --- |
| No model profiles | Check `llm.providers`, `active_provider`, and `active_model` |
| API key missing | Check the referenced environment variable, such as `KAIRO_DEEPSEEK_API_KEY` |
| Sessions not saved | Check `sessions.enabled`, `sessions.storage_dir`, and directory write permissions |
| Workspace did not move | Run `/workspace`, then retry `/workspace move <path>` with an existing writable directory |
| TUI is unstable | Start with `kairo --plain` or `kairo --reduced-motion` |
| Too many tool prompts | Use `/auto` only if workspace-internal automation is acceptable |
| Too much automation | Use `/manual` |
