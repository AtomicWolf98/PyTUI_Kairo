# Kairo 文档

本文档系统按读者任务组织。

| 你要做什么 | 阅读 |
| --- | --- |
| 了解当前进度、验证基线和接手顺序 | [项目状态](project-status.md) |
| 安装并开始使用 | [用户指南](user-guide.md) |
| 查询 Slash Command 和快捷键 | [命令参考](commands.md) |
| 添加或切换模型、理解配置字段 | [配置指南](configuration.md) |
| 理解 Agent、TUI、工具、授权和上下文流 | [系统架构](architecture.md) |
| 修改代码、加工具、跑测试 | [开发者指南](developer-guide.md) |
| 定位启动、配置、TUI 或 Workspace 问题 | [故障排查](troubleshooting.md) |
| 处理凭据或安全问题 | [安全说明](../SECURITY.md) |

文档与代码同仓维护。涉及命令、配置字段、模块边界或运行流程的代码变更，应在同一个提交中更新对应文档。

## 目录边界

- 当前主目录：开发文档的唯一维护位置，包含架构、开发指南、项目状态和交接信息。
- `PyTUI_Kairo/`：GitHub 发行目录，只保留 README、Changelog、安全说明及用户指南、命令、配置和故障排查。

开发文档不得再次整套复制到发行目录；用户可见行为发生变化时，只同步对应的用户文档。
