# Kairo

Kairo 是一个终端原生的 AI coding agent，在 Windows / Linux / macOS 的终端中提供全屏 TUI 与兼容的 plain 模式。它连接 OpenAI-compatible 的大模型，通过文件、Shell、Python、Web 和自定义 skill 工具帮你完成本地开发任务。

当前版本：`0.2.1`

## 核心特性

- **全屏 TUI + Plain 模式**：默认启动 Textual 全屏界面；无 TTY 或需要重定向时自动 fallback 到 plain，也可通过 `--tui` / `--plain` 强制指定。
- **多级授权**：
  - `manual`：每条工具都确认（默认）。
  - `auto`：工作区内的常规工具自动执行；外部/系统/危险操作仍要确认。
  - `yolo`：全部自动执行，适合用户离开设备、需要长时间自主运行的场景。
- **Workspace 管理**：`/workspace move <path>` 随时切换工作目录；右侧 Dock 实时显示新目录文件树、Git 改动和只读 Diff。
- **多行 Composer**：`Shift+Enter` 或 `Ctrl+Enter` 换行，输入栏按视觉行数自动增高。
- **宽内容显示**：长文本和 Markdown 表格自动折行，代码与 Diff 保持可滚动浏览。
- **Plan / Thinking 模式**：先规划后执行，或显示模型的 reasoning 过程。
- **上下文治理**：自动估算、压缩与裁剪，保护系统提示和当前用户轮次。
- **多会话**：进程内多会话切换，关闭后失效（尚未持久化）。
- **自定义 skills**：`./skills` 目录下动态加载本地工具。

## 快速开始

### Windows

```powershell
# 双击运行，会自动创建 .venv、安装依赖并启动
.\run.bat

# 或从 PowerShell 手动
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

### 首次配置

```powershell
# 复制示例配置
cp config.example.json config.json

# 设置至少一个 provider 的密钥环境变量
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"

# 启动
kairo
```

推荐优先使用 `provider.api_key_env` 指向环境变量，而不是把密钥写入 `config.json`。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `/manual` `/auto` `/yolo` | 切换授权级别 |
| `/plan` | 切换 Plan Mode |
| `/think` | 切换 Thinking Mode |
| `/workspace` | 显示当前 workspace |
| `/workspace move <path>` | 切换 workspace |
| `/model` | 选择 provider / model |
| `/sessions` | 切换会话 |
| `/help` | 查看完整命令 |

快捷键：

- `Shift+Enter` / `Ctrl+Enter`：输入换行
- `Ctrl+A`：循环授权级别（manual → auto → yolo）
- `Ctrl+P`：切换 Plan Mode
- `Ctrl+T`：切换 Thinking Mode
- `Ctrl+B`：Workspace 焦点切换

## 启动参数

| 参数 | 说明 |
| --- | --- |
| `--config PATH` | 指定配置文件，默认 `config.json` |
| `--authorization {manual,auto,yolo}` | 设置启动授权级别 |
| `--auto` | 等价于 `--authorization auto` |
| `--plan` | 启动时开启 Plan Mode |
| `--think` | 启动时开启 Thinking Mode |
| `--plain` | 强制 plain 模式 |
| `--tui` | 强制 TUI 模式 |

## 安全提示

- Kairo 可以执行 Shell 命令和 Python 代码。默认 `manual` 级别会逐条确认。
- `auto` 级别只对工作区内部操作免确认；涉及系统安装、删除、网络、工作区外路径时仍会弹窗。
- `yolo` 会跳过工具确认，但路径、网络、命令和资源策略仍然生效；只在可信环境和明确需求下使用。
- 密钥建议通过环境变量提供，`Config.save()` 不会把环境变量注入的密钥写回磁盘。
- 工具的沙箱是语法/策略层过滤，不是进程级隔离；处理不可信代码或高敏感数据时，请在虚拟机/容器中使用。

## 文档

- [用户指南](docs/user-guide.md)
- [命令参考](docs/commands.md)
- [配置指南](docs/configuration.md)
- [故障排查](docs/troubleshooting.md)
- [安全说明](SECURITY.md)

## 许可证

[MIT](LICENSE)
