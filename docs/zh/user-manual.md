# Kairo 完整用户手册

版本：**0.2.2**

Kairo 是一个终端原生的 AI coding agent。它默认使用 Textual 全屏 TUI，也支持 `--plain` 兼容模式；可以连接 OpenAI-compatible 模型，对本地 workspace 进行文件读写、搜索、patch、Shell、Python、Web fetch、上下文压缩、会话持久化和自定义 skill 调用。

## 1. 安装与启动

### Windows

```powershell
.\run.bat
```

手动安装：

```powershell
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

### 常用启动参数

| 参数 | 作用 |
| --- | --- |
| `--config <path>` | 指定配置文件 |
| `--plain` | 使用兼容的非全屏输出 |
| `--tui` | 即使在非标准环境中也强制使用 Textual TUI |
| `--no-animation` | 关闭动画 |
| `--reduced-motion` | 使用低动效 |
| `--authorization manual|auto|yolo` | 指定授权级别 |
| `--auto` | 快捷进入 `auto` 授权 |
| `--plan` | 启动时开启 Plan Mode |
| `--think` | 启动时开启 Thinking Mode |

## 2. 首次配置

复制配置模板：

```powershell
Copy-Item config.example.json config.json
```

推荐使用环境变量保存 API Key：

```powershell
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
```

`config.json` 中的 provider 示例：

```json
{
  "llm": {
    "active_provider": "deepseek",
    "active_model": "deepseek-chat",
    "providers": [
      {
        "name": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "KAIRO_DEEPSEEK_API_KEY",
        "models": [
          {
            "name": "deepseek-chat",
            "temperature": 0.2,
            "max_tokens": 8000,
            "context_window": 128000
          }
        ]
      }
    ]
  }
}
```

## 3. 界面说明

Kairo 有两种界面：

- **Textual TUI**：默认模式。包含对话区、输入栏、Slash 命令菜单、Kai 状态动画、Workspace Dock 和上下文进度条。
- **Plain 模式**：用 `--plain` 启动，适合不支持全屏 TUI 的终端、CI、日志重定向或调试。

宽屏下右侧 Dock 会显示 workspace 文件树、会话触达文件、Git/非 Git Diff、模型、会话、上下文、token、模式和任务状态。窄屏下会折叠为底部状态栏，可用 `/workspace` 或 `Ctrl+B` 打开 workspace 视图。

## 4. Slash 命令

输入 `/` 会打开命令菜单。继续输入会按前缀过滤；`Up/Down` 选择，`Tab` 或 `Enter` 补全，`Esc` 关闭。

| 命令 | 作用 | 使用示例 |
| --- | --- | --- |
| `/help` | 显示帮助 | `/help` |
| `/exit` | 退出 Kairo | `/exit` |
| `/config` | 显示当前配置 | `/config` |
| `/model` | 从配置好的模型 profile 中选择 | `/model` |
| `/manual` | 每个工具调用都需要确认 | `/manual` |
| `/auto` | workspace 内常规工具自动执行，外部/系统/危险操作仍确认 | `/auto` |
| `/yolo` | 跳过工具确认；仍应谨慎使用 | `/yolo` |
| `/plan` | 开关 Plan Mode | `/plan` |
| `/think` | 开关 Thinking Mode | `/think` |
| `/skills` | 查看已加载工具和自定义 skills | `/skills` |
| `/new [名称]` | 创建并切换到新持久化会话 | `/new Refactor auth` |
| `/sessions` | 切换已保存会话 | `/sessions` |
| `/clear` | 清空当前会话，不删除会话文件 | `/clear` |
| `/undo` | 撤销当前会话最近一轮用户输入及后续回复 | `/undo` |
| `/compress` | 手动压缩较早上下文，保留近期轮次 | `/compress` |
| `/workspace` | 显示当前 workspace | `/workspace` |
| `/workspace move <path>` | 热切换 workspace，无需重启 | `/workspace move C:\repo\app` |

## 5. 快捷键与输入

| 快捷键 | 作用 |
| --- | --- |
| `Enter` | 提交当前输入 |
| `Shift+Enter` / `Ctrl+Enter` | 在输入栏插入换行 |
| `Ctrl+Up` / `Ctrl+Down` | 浏览输入历史 |
| `Ctrl+B` | 宽屏下切换 Workspace Dock 焦点；窄屏下打开 Workspace Modal |
| `Ctrl+A` | 循环切换 `manual -> auto -> yolo` |
| `Ctrl+P` | 开关 Plan Mode |
| `Ctrl+T` | 开关 Thinking Mode |
| `Esc` | 关闭命令菜单或弹窗 |

输入栏会随着文本行数或软换行自动增高，最多显示 8 行；更长内容可继续滚动编辑。

## 6. 模型 Profile

Kairo 不再使用固定模型列表，而是从 `config.json` 的 `llm.providers[].models[]` 读取可选 profile。使用 `/model` 打开选择菜单。切换后：

- 当前模型、base URL、temperature、max tokens、context window 立即更新。
- Dock 的上下文限制立即重新计算。
- 所有会话的 runtime state 会保存当前模型 profile。

## 7. 会话持久化

0.2.2 起，Kairo 支持持久化 session。默认配置：

```json
"sessions": {
  "enabled": true,
  "storage_dir": ".kairo/sessions",
  "autosave": true,
  "save_interval_seconds": 1.0,
  "max_sessions": 200
}
```

行为：

- 每个 session 保存为独立 JSON 文件。
- `index.json` 记录会话列表和最后活动会话。
- `/new` 创建新 session 文件。
- `/sessions` 在已保存 session 之间切换。
- 普通回复、工具结果、压缩、撤销、清空、模型切换、workspace 切换和退出都会触发保存。
- `sessions.enabled=false` 时退回纯内存会话。

注意：session 文件可能包含提示词、代码片段、命令输出、文件内容或敏感信息。默认 `.kairo/` 已加入 `.gitignore`，不建议提交到仓库。

## 8. 上下文管理

Dock 显示：

```text
Context: ≈used / limit (percent)
```

功能：

- `session_input_tokens/session_output_tokens`：累计 token 统计。
- `context_used_tokens`：当前真实会发送给模型的上下文估算。
- `/compress`：手动压缩较早历史，保留主系统提示、runtime state 和最近轮次。
- 自动压缩：达到阈值或剩余空间不足时触发。
- 自动裁剪：压缩失败或仍然超限时按完整对话轮次裁剪旧内容。

颜色含义：

- 低于 60%：正常。
- 60% 到自动压缩阈值：警告。
- 达到自动压缩阈值：高风险，可能触发压缩。

## 9. Workspace 与 Dock

Workspace 是 Kairo 认为可以安全操作的项目根目录。配置项：

```json
"workspace_root": "."
```

常用操作：

```text
/workspace
/workspace move C:\Users\Admin\Desktop\project\my-app
```

0.2.2 的热切换行为：

- 文件工具、patch/search 工具会更新到新 root。
- Shell 持久会话会在新 workspace 下重启。
- Python REPL 会重置，避免旧变量和旧路径泄漏。
- 自定义 skills 会从新 workspace 重新加载。
- 当前会话的 runtime state 会写入新 workspace，下一次 LLM 请求立即知道当前 root。
- Dock 文件树和 Diff 会刷新。

Dock 中的 Workspace Review 是只读审查，不会执行 git add、恢复、删除或覆盖操作。

## 10. 工具与授权

Kairo 内置工具：

| 工具 | 能力 |
| --- | --- |
| `read_file` | 读取 workspace 内文件 |
| `write_file` | 写入或覆盖文件 |
| `list_dir` | 列出目录 |
| `search_file` | 搜索文本或正则 |
| `patch_file` | 对文件执行精确 search/replace patch |
| `run_command` | 在持久 Shell 中运行命令 |
| `run_python_code` | 在受限 Python REPL 中运行代码 |
| `web_fetch` | 抓取网页内容 |
| custom skills | 从 `skills_dir` 加载用户定义工具 |

授权级别：

- `manual`：所有工具都询问。
- `auto`：workspace 内普通操作自动执行；外部、系统、破坏性操作仍询问。
- `yolo`：跳过确认，适合用户明确接受风险的长任务。

## 11. 自定义 Skills

默认目录：

```json
"skills_dir": "./skills"
```

相对路径会按当前 workspace 解析。workspace move 后会重新加载新 workspace 下的 skills，并卸载旧 workspace 的 custom skills。

最小示例：

```python
from tools.base import skill

@skill(name="hello_skill", description="Return a greeting")
def hello_skill(name: str = "Kairo"):
    return f"Hello, {name}"
```

保存为 `skills/hello_skill.py` 后重启，或切换 workspace 触发 reload。

## 12. 版本历史

### 0.2.2

新增：

- session 持久化：独立 JSON 文件、`index.json`、自动恢复最后活动会话。
- `sessions` 配置：`enabled`、`storage_dir`、`autosave`、`save_interval_seconds`、`max_sessions`。
- workspace move 当前会话热生效：runtime state、工具 root、Shell cwd、Python REPL reset、skills reload。
- session store 和 workspace hot-switch 测试。

变更：

- `/new` 和 `/sessions` 从进程内会话升级为持久化会话。
- `/workspace move <path>` 不再只是保存配置，而是当前进程立即生效。
- 用户文档更新为说明持久化与热切换行为。

修复：

- 搜索结果相对路径以当前 workspace root 为基准。
- 模型请求不再依赖旧对话中残留的 workspace 描述。

### 0.2.1

新增：

- 三档授权：`manual`、`auto`、`yolo`。
- `/workspace move <path>`。
- 可滚动 Slash Command 菜单。
- Workspace Dock：文件树、会话触达文件、Git/非 Git Diff。
- 响应式 Dock 宽度、上下文进度条、多行输入、宽文本渲染优化。

修复：

- `/config` 在 Textual UI 中崩溃的问题。
- workspace 快速切换导致旧扫描覆盖新 Dock 的问题。
- 相同文件结构切换 workspace 时文件树不刷新的问题。
- Windows 下 `Shift+Enter` / `Ctrl+Enter` 修饰键丢失的问题。

### 0.2.0

新增：

- Kairo 品牌和 Kai 终端吉祥物。
- Textual 全屏 TUI、动画、右侧 Dock、plain fallback。
- 模型 profile、上下文管理、进程内多会话。
- `/compress`、`/new`、`/sessions`。

### 0.1.0

新增：

- pyTUI 原型。
- Rich CLI、OpenAI-compatible 流式客户端、基础本地工具。

## 13. 常见问题

### `/model` 没有可选模型

检查 `config.json` 的 `llm.providers` 是否有 provider 和 models，并确认 `active_provider`、`active_model` 与名称匹配。

### API Key 没生效

优先检查环境变量是否存在：

```powershell
echo $env:KAIRO_DEEPSEEK_API_KEY
```

### session 没有保存

确认：

- `sessions.enabled` 为 `true`。
- `sessions.storage_dir` 可写。
- 没有把 Kairo 放在只读目录中运行。

### workspace 切换后 Dock 没更新

使用 `/workspace` 确认当前 root。若 Dock 短暂为空，通常是后台扫描尚未完成；等待一次刷新即可。若路径不存在或不可写，`/workspace move` 会失败并提示原因。

### TUI 不适配当前终端

使用：

```powershell
kairo --plain
```

或：

```powershell
kairo --reduced-motion
```

### 需要避免自动工具执行

使用：

```text
/manual
```

或启动参数：

```powershell
kairo --authorization manual
```
