# 命令参考

命令元数据的唯一代码来源是 `agent/commands.py`。新增或修改命令时，应同时更新该文件和本页。

## Slash Command

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助信息 |
| `/exit` | 退出 Kairo |
| `/manual` | 授权级别设为 manual（逐条确认） |
| `/auto` | 授权级别设为 auto（工作区内自动，外部/系统/危险仍确认） |
| `/yolo` | 授权级别设为 yolo（全部自动，高风险） |
| `/plan` | 切换 Plan Mode（先规划后执行） |
| `/think` | 切换 Thinking Mode（显示 reasoning） |
| `/skills` | 列出已加载的工具和 skills |
| `/clear` | 清空当前会话历史 |
| `/compress` | 手动压缩当前会话早期上下文 |
| `/new [名称]` | 创建并切换到新会话 |
| `/sessions` | 切换已有会话 |
| `/config` | 显示当前配置 |
| `/model` | 交互式选择 provider / model |
| `/undo` | 撤销当前会话最后一轮 |
| `/workspace` | 显示当前 workspace 路径 |
| `/workspace move <path>` | 切换 workspace 到指定目录 |

## 快捷键

| 按键 | 行为 |
| --- | --- |
| `Enter` | 提交输入；命令菜单打开时补全高亮项 |
| `Shift+Enter` | 插入换行 |
| `Tab` | 补全高亮命令 |
| `Up` / `Down` | 命令菜单中循环选择并自动滚动 |
| `Esc` | 关闭命令菜单，保留输入 |
| `Ctrl+Up` / `Ctrl+Down` | 浏览本次进程的输入历史 |
| `Ctrl+B` | Workspace 焦点切换；窄屏打开 Workspace Modal |
| `Ctrl+A` | 循环授权级别（manual → auto → yolo） |
| `Ctrl+P` | 切换 Plan Mode |
| `Ctrl+T` | 切换 Thinking Mode |

命令菜单固定显示约 7 行，完整目录已载入，可用鼠标滚轮浏览。
