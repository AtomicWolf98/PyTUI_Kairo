# 用户指南

本文档面向最终用户，帮助你安装、配置和日常使用 Kairo。

## 安装与启动

### Windows（推荐）

```powershell
# 自动创建 .venv、安装依赖并启动
.\run.bat

# 后续可以直接用虚拟环境启动
. .venv/Scripts/activate
kairo
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
kairo
```

### 强制界面模式

```powershell
# 强制 TUI，即使在 IDE 集成终端中
kairo --tui

# 强制纯文本兼容模式
kairo --plain
```

启动时 Kairo 会检测 stdin/stdout 是否为 TTY。若被重定向或运行在 `TERM=dumb` 环境，会自动进入 plain 模式；加上 `--tui` 可强制启动 Textual 界面。

## 首次配置

1. 复制示例配置：

```powershell
cp config.example.json config.json
```

2. 设置环境变量（推荐）：

```powershell
$env:KAIRO_DEEPSEEK_API_KEY = "sk-..."
$env:KAIRO_MINIMAX_API_KEY = "sk-..."
```

3. 按需修改 `config.json` 中的 `llm.active_provider`、`llm.active_model` 等字段。

> 不要把密钥写入仓库脚本。`config.json` 已被 `.gitignore` 忽略，但环境变量更安全。

## 授权级别

Kairo 有三种授权级别，控制工具调用前是否弹窗确认：

| 级别 | 行为 | 适用场景 |
| --- | --- | --- |
| `manual` | 每条工具都确认 | 默认，最保守 |
| `auto` | 工作区内部工具自动执行；外部/系统/危险操作仍确认 | 日常高效使用 |
| `yolo` | 所有工具自动执行，无确认 | 用户离开设备、需要长时间自主运行 |

切换方式：

- 命令：`/manual`、`/auto`、`/yolo`
- 快捷键：`Ctrl+A` 循环切换
- 启动参数：`kairo --authorization auto` 或 `kairo --auto`

状态栏会用颜色显示当前级别：白色（manual）、黄色（auto）、红色（yolo）。

## 三种模式

- **Plan Mode**（`/plan` 或 `Ctrl+P`）：Agent 先输出实施计划，等待你确认后再执行工具。
- **Thinking Mode**（`/think` 或 `Ctrl+T`）：显示 API 原生 reasoning 内容或 `<think>` 包裹的推理。
- **Auto 授权**不是模式，而是授权级别，见上表。

## Workspace 与改动审查

- 宽屏右侧 Dock 显示当前 workspace 的文件树、Git 改动、会话触达文件和只读 Diff。
- `Ctrl+B` 在 Composer 与 Workspace 之间切换焦点；窄屏会打开全屏 Workspace Modal。
- `/workspace` 显示当前 workspace 路径。
- `/workspace move <path>` 切换到另一个目录，工具边界会立即跟随新目录生效。

切换成功后 Header、右侧文件树、改动列表和 Diff 会共同刷新。快速连续切换时，过期目录扫描结果不会覆盖最新 workspace。

Workspace 审查是只读的，不会暂存、恢复或改写文件。

## 会话与上下文

- `/new [名称]` 创建新会话，`/sessions` 切换会话。
- 会话只存在于内存中，关闭 Kairo 后不会恢复。
- Dock 中的 `Context` 进度条显示即将发送给模型的估算 token 占用。
- 达到阈值后 Kairo 会自动压缩早期轮次；必要时按完整轮次裁剪，始终保护系统提示和当前用户轮次。
- `/compress` 手动触发压缩，`/clear` 清空当前会话，`/undo` 撤销当前会话最后一轮。

## Composer 键盘

| 按键 | 行为 |
| --- | --- |
| `Enter` | 提交；打开命令菜单时补全高亮项 |
| `Shift+Enter` | 插入换行 |
| `Ctrl+Enter` | 插入换行 |
| `Tab` | 补全高亮命令 |
| `Up` / `Down` | 命令菜单中循环选择 |
| `Esc` | 关闭命令菜单 |
| `Ctrl+Up` / `Ctrl+Down` | 浏览本次进程的输入历史 |
| `Ctrl+B` | Workspace 焦点切换 |
| `Ctrl+A` | 循环授权级别 |
| `Ctrl+P` | 切换 Plan Mode |
| `Ctrl+T` | 切换 Thinking Mode |

Composer 会按照显式换行和自动折行后的视觉行数增高，最多显示 8 行；超过后可在输入框内部滚动。普通长文本和 Markdown 表格会在对话区折行显示。

## 常见问题速查

**启动后进入 plain 模式**

尝试 `kairo --tui`。若仍不行，检查 `config.json` 中 `ui.mode` 是否为 `plain`，或环境变量 `TERM` 是否为 `dumb`。

**工具一直等待确认**

当前是 `manual` 或 `auto` 级别且操作被判定为外部/系统/危险。在弹窗中选择运行、跳过或提升授权级别。

**请求返回 401/403**

确认活动 provider 的 `api_key_env` 所指环境变量已设置，且 `base_url` 正确。

**Workspace 没有文件**

- Git 仓库需 Git 命令可用。
- 非 Git 目录只能显示会话期间被触达的文件。
- `/workspace move <path>` 可切换到正确目录。

更多排查见 [故障排查](troubleshooting.md)。
