# Kairo 完整用户手册

版本：**0.2.7-beta**

Kairo 是一个终端原生的 AI coding agent。它默认使用 Textual 全屏 TUI，也支持 `--plain` 兼容模式；可以连接 OpenAI-compatible 模型，对本地 workspace 进行文件读写、搜索、patch、Shell、Python、Web fetch、上下文压缩、会话持久化、自定义 skill 调用，并支持在 TUI 内运行时配置 provider 和 model。

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

推荐使用环境变量保存 API Key（多人或 CI 共享项目时建议）：

```powershell
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
```

`config.json` 中的 profile 示例：

```json
{
  "llm": {
    "active_profile": "deepseek-chat",
    "profiles": [
      {
        "id": "deepseek-chat",
        "name": "DeepSeek Chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "KAIRO_DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 8000,
        "context_window": 128000
      }
    ],
    "model_roles": {
      "chat": "deepseek-chat",
      "plan": "deepseek-chat",
      "compress": "deepseek-chat",
      "fast": "deepseek-chat"
    }
  }
}
```

优先使用 `api_key_env`，避免把 secret 落盘。若仅本地使用，也可以用 inline `api_key`；在 `/settings` > Keys 中安全地管理 key。

旧版 `llm.providers[]` 配置仍会被自动转换为 profile 继续使用。

## 3. 界面说明

Kairo 有两种界面：

- **Textual TUI**：默认模式。包含对话区、输入栏、Slash 命令菜单、Kai 状态动画、Workspace Dock 和上下文进度条。
- **Plain 模式**：用 `--plain` 启动，适合不支持全屏 TUI 的终端、CI、日志重定向或调试。

宽屏下右侧 Dock 会显示 workspace 文件树、会话触达文件、Git/非 Git Diff、模型、会话、上下文、token、模式和任务状态。窄屏下会折叠为底部状态栏，可用 `/workspace` 或 `Ctrl+B` 打开 workspace 视图。

## 4. Slash 命令

输入 `/` 会打开命令菜单。继续输入会按前缀过滤；`Up/Down` 选择，`Tab` 或 `Enter` 补全，`Esc` 关闭。

0.2.7-beta 将默认 slash 命令从 52 条收敛到 18 条工作流入口。provider/model/key/session/config 的细粒度命令已迁移到交互面板。

| 命令 | 作用 | 使用示例 |
| --- | --- | --- |
| `/help` | 显示分组帮助 | `/help` |
| `/exit` | 退出 Kairo | `/exit` |
| `/new [名称]` | 创建并切换到新持久化会话 | `/new Refactor auth` |
| `/sessions` | 打开会话管理面板 | `/sessions` |
| `/clear` | 清空当前会话，不删除会话文件 | `/clear` |
| `/undo` | 撤销当前会话最近一轮用户输入及后续回复 | `/undo` |
| `/compress` | 手动压缩较早上下文，保留近期轮次 | `/compress` |
| `/model` | 切换当前 chat profile | `/model` |
| `/setup` | 运行首次配置向导 | `/setup` |
| `/settings` | 打开设置/配置面板 | `/settings` |
| `/mode` | 打开模式面板（授权/Plan/Thinking） | `/mode` |
| `/workspace [path-or-bookmark]` | 打开 workspace 面板或热切换 workspace | `/workspace C:\repo\app` |
| `/status` | 显示只读运行状态 | `/status` |
| `/find <keyword>` | 搜索当前会话与持久化会话 | `/find auth` |
| `/export` | 导出会话或配置 | `/export` |
| `/doctor` | 运行健康检查 | `/doctor` |
| `/skills` | 查看已加载工具和自定义 skills | `/skills` |
| `/docs` | 显示本地文档索引 | `/docs` |

### 已删除命令与迁移（0.2.7-beta）

| 已删除命令 | 替代方式 |
| --- | --- |
| `/manual` `/auto` `/yolo` `/plan` `/think` | `/mode` |
| `/providers` `/provider add|edit|remove|test` | `/settings` > Providers |
| `/model add|edit|remove|test` | `/settings` > Models |
| `/keys` `/key set|clear|reveal|migrate` | `/settings` > Keys |
| `/roles` `/role set|clear` | `/settings` > Roles |
| `/config validate|backup|restore|export|import` | `/settings` > Config 或 `/export` |
| `/session rename|delete|export|reveal|search|open` | `/sessions` |
| `/workspace save` `/workspaces` `/workspace remove` | `/workspace` |
| `/docs config` `/docs providers` `/docs sessions` | `/docs` |

`/model` 现在仅用于切换 chat profile；provider/model/key/role/config 管理都在 `/settings` 中。`/workspace move <path>` 现在是 `/workspace <path-or-bookmark>`。

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
| `Esc` | 关闭命令菜单或弹窗；忙时（流式输出/工具运行中）停止当前输出（0.2.6） |

输入栏会随着文本行数或软换行自动增高，最多显示 8 行；更长内容可继续滚动编辑。

## 6. 模型 Profile

0.2.5 起 Kairo 从 `config.json` 的 `llm.profiles[]` 读取可选 profile；旧版 `llm.providers[].models[]` 仍兼容。使用 `/model` 打开选择菜单。0.2.6 起 `/model` 是单一事务，切换的是 **chat profile**：会保持 `llm.active_profile` 与 `model_roles.chat` 一致，并同步 context window、runtime state 与所有 session，下一次 chat 请求必定使用新 profile。切换后：

- 当前模型、base URL、temperature、max tokens、context window 立即更新。
- Dock 的上下文限制立即重新计算。
- 会话 runtime state 会保存当前模型 profile。

### 6.1 模型角色（0.2.5）

`llm.model_roles` 可把不同任务路由到不同 profile：

```json
"model_roles": {
  "chat": "deepseek-chat",
  "plan": "deepseek-reasoner",
  "compress": "deepseek-chat",
  "fast": "local-llm"
}
```

角色说明：

- `chat` — 默认用户对话。
- `plan` — Plan/Thinking 模式。
- `compress` — 上下文压缩总结。
- `fast` — 快速内部任务。

使用 `/settings` > Roles 绑定或解除角色。未绑定的角色使用当前 active profile。

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
/workspace C:\Users\Admin\Desktop\project\my-app
```

0.2.2 的热切换行为：

- 文件工具、patch/search 工具会更新到新 root。
- Shell 持久会话会在新 workspace 下重启。
- Python REPL 会重置，避免旧变量和旧路径泄漏。
- 自定义 skills 会从新 workspace 重新加载。
- 当前会话的 runtime state 会写入新 workspace，下一次 LLM 请求立即知道当前 root。
- Dock 文件树和 Diff 会刷新。

### 9.1 Workspace 书签（0.2.5）

收藏常用的 workspace：

```text
/workspace
/workspace C:\Users\Admin\Desktop\project\app
/workspace app
```

`/workspace` 面板可保存、重命名、移除书签。书签保存在 `config.json` 的 `workspace_bookmarks` 中，跨重启生效。使用 `/workspace <bookmark-name>` 可快速切换。

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

### 0.2.5

新增：

- `llm.profiles[]`、`active_profile`、`model_roles` 与 `ProfileResolver`。
- `/keys`、`/key set|clear|reveal|migrate` 命令。
- `/roles`、`/role set|clear` 命令。
- `workspace_bookmarks` 与 `/workspace save|remove`、`/workspaces` 命令。
- `/session search` 与 `/session open` 只读会话查询。
- `/config export` 与 `/config import`，默认对 key 脱敏。
- `/doctor` 健康检查面板。
- 0.2.5 新功能的测试覆盖。

变更：

- 本地部署默认允许 inline API key；env key 仍可用于多人/CI 场景。
- `/config`、日志、会话历史、`/doctor` 默认掩码显示 API key。
- `config.example.json` 改为新的 `llm.profiles[]` 格式。

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

检查 `config.json` 的 `llm.profiles[]` 或旧版 `llm.providers[]`、profile ID、`active_profile`，以及 `llm.model_roles` 的角色绑定。

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

使用 `/workspace` 确认当前 root。若 Dock 短暂为空，通常是后台扫描尚未完成；等待一次刷新即可。若路径不存在或不可写，`/workspace <path>` 会失败并提示原因。

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
/mode
```

选择 Manual 授权，或启动参数：

```powershell
kairo --authorization manual
```

## 8. 运行时配置与面板（0.2.7-beta）

Kairo 支持在已启动的 TUI 内增删改模型 profile，无需退出程序或编辑 `config.json`。旧版 `llm.providers[]` 配置会被自动转换为 profile。0.2.7-beta 起，所有 provider/model/key/role/config 的细粒度命令被迁移到交互面板；slash 命令收敛为工作流入口。

### 8.1 `/settings` — 配置面板

`/settings` 打开集中配置面板，覆盖原先分散在 `/providers`、`/model`、`/keys`、`/roles`、`/config` 中的能力：

- **Providers**：列出、新增、编辑、删除、测试 provider。
- **Models**：列出、新增、编辑、删除、测试 model。
- **Keys**：列出 key 来源、设置 inline key、清除 key、reveal、migrate 旧版 key。
- **Roles**：绑定或解绑 `chat`/`plan`/`compress`/`fast` 角色到 profile。
- **Config**：校验、备份、恢复、导入、导出当前配置。

provider/model 测试会发送最小 OpenAI-compatible 探测请求；测试不会写入 session history，也不会触发上下文压缩。

### 8.2 `/setup` — 首次配置向导

`/setup` 运行首次配置向导，适用于新安装或 active profile 无效时。它会引导创建 profile/provider、设置 base URL、model、API key 模式（inline/env）、参数，然后运行最小连接测试并保存配置，保存前自动生成备份。

### 8.3 `/mode` — 授权与模式

`/mode` 统一替代 `/manual`、`/auto`、`/yolo`、`/plan`、`/think`。打开紧凑面板，可设置：

- **Authorization**：Manual / Auto / YOLO。
- **Plan Mode**：ON / OFF。
- **Thinking Mode**：ON / OFF。

### 8.4 `/status` — 运行状态

`/status` 显示只读运行状态摘要：

- Kairo 版本。
- 当前 chat profile、model、base URL。
- API key 来源与脱敏状态。
- 当前 session 名称、id、消息数。
- 上下文已用 / 窗口 / 百分比。
- Workspace root。
- Plan / Thinking / Authorization 状态。
- Session persistence 与 strict message packing 状态。

不显示完整 API key 或未脱敏 config JSON。

### 8.5 `/sessions` — 会话管理

`/sessions` 打开会话管理面板，覆盖原先分散在 `/session` 子命令中的能力：

- 切换当前会话。
- 按标题或内容搜索会话（`/find <keyword>` 是快捷入口）。
- 从搜索结果打开会话。
- 重命名或删除会话（最后一个 active session 不可删除）。
- 导出当前会话为 Markdown 或 JSON。
- 显示当前会话文件的绝对路径。

### 8.6 `/workspace [path-or-bookmark]` — Workspace 面板与热切换

`/workspace` 无参数时打开 workspace 面板，显示当前 root、书签、文件树、变更文件与 diff。带参数时在当前进程热切换到指定路径或书签名：文件工具、Shell cwd、Python REPL、自定义 skills、Dock 文件树、会话 runtime state 都会更新，无需重启 Kairo。

旧版本的 `/workspace move <path>` 现在统一为 `/workspace <path-or-bookmark>`。

### 8.7 `/export` — 统一导出

`/export` 打开统一导出面版：

- 当前会话导出为 Markdown。
- 当前会话导出为 JSON。
- 配置导出，默认脱敏。
- 含 key 的配置导出，需要二次确认。

### 8.8 API Key 安全

- **本地部署默认**：允许在 `config.json` 中保存 inline `api_key`。请勿将 `config.json` 纳入版本控制或提交到仓库。
- **多人或 CI 项目推荐**：使用 `api_key_env` 并在系统环境变量中设置 key。env key **不会**被写回 `config.json`。
- Key reveal 与含 key 的配置导出需要二次确认。
- `/status`、日志、会话历史、`/doctor` 只显示掩码预览，不会输出完整 key。

### 8.9 运行时配置的实现方式

运行时配置不是直接让用户手改 JSON，而是通过“命令 -> 面板 -> 草稿 -> 校验 -> 备份 -> 保存 -> 热切换”的安全流程完成。

1. **命令入口**：输入 `/settings` 或 `/setup`。
2. **界面收集信息**：Textual TUI 中打开 Modal 表单；Plain 模式中用逐步问答收集同样字段。
3. **写入 ConfigDraft**：表单内容先写入内存中的 `ConfigDraft`，不会立刻覆盖 `config.json`。
4. **校验配置**：保存前检查 provider/profile 名称是否重复、base URL 是否合法、active profile 是否存在、`context_window`、`max_tokens` 和 `temperature` 是否合理。
5. **API Key 处理**：选择 `env` 时只保存 `api_key_env` 名称；选择 `inline` 时会要求确认，因为 key 会写入磁盘。
6. **自动备份**：保存前生成 `config.backup.YYYYMMDD-HHMMSS.json`。
7. **原子保存与回滚**：Kairo 使用临时文件写入再替换原配置；如果保存失败，会保留原配置。
8. **立即生效**：保存成功后重新加载 active profile，更新 `base_url`、`model`、`temperature`、`max_tokens`、`context_window` 和上下文管理参数。
9. **会话联动**：当前会话的 runtime state 会记录新的模型 profile；Dock 中的模型名和上下文窗口也会刷新。

因此，运行时配置的结果和手动编辑 `config.json` 等价，但多了校验、备份、API Key 安全处理和当前会话热更新。

### 8.10 `/doctor`

`/doctor` 运行健康检查面板，检查配置合法性、key 是否存在、workspace 是否可达、session 存储、git 状态以及 provider 连通性。不会输出任何 secret。

## 9. 0.2.7-beta 更新内容

- **Slash 命令重构**：默认 slash 命令从 52 条收敛到 18 条工作流入口。
- **面板化管理**：provider/model/key/role/config 管理迁移到 `/settings`；会话管理迁移到 `/sessions`；workspace 管理迁移到 `/workspace`。
- **新增命令**：`/setup`（首次配置向导）、`/mode`（授权/Plan/Thinking）、`/status`（只读运行状态）、`/find`（会话搜索）、`/export`（统一导出）。
- **删除子命令**：`/manual`、`/auto`、`/yolo`、`/plan`、`/think`、`/provider ...`、`/model add|edit|remove|test`、`/key ...`、`/role ...`、`/config ...`、`/session ...`、`/workspace save|remove`、`/docs config|providers|sessions`。已删除命令现在返回迁移提示。
- **`/workspace <path-or-bookmark>`**：旧版 `/workspace move <path>` 现在统一为 `/workspace` 的参数形式。
- **`/model` 仅切换**：用于选择 chat profile；编辑管理请使用 `/settings`。

## 10. 0.2.6-beta 更新内容

- **统一 `/model` 切换**：`/model` 以单一事务切换 chat profile，保持 `model_roles.chat`、`active_profile`、context window 与 session 一致，下一次 chat 请求必定使用新 profile。
- **Provider key 保留**：编辑某个 provider 不再清空其它 provider 的 inline key。留空保留原 key；显式 clear 只清空目标。
- **严格消息打包**：所有 LLM 请求 payload 折叠为唯一首位 `system` 消息，兼容严格 OpenAI-compatible provider（`llm.strict_message_packing`，默认 `true`）。
- **Esc 停止输出**：Textual 模式下流式输出/工具运行中按 `Esc` 协作停止当前输出，partial 回复以 `[stopped]` 标记保存；plain 模式仍使用 `Ctrl+C`。

## 11. 0.2.5 更新内容

- Profile-first 配置：`llm.profiles[]`，并自动兼容旧版 `llm.providers[]`。
- 本地配置优先的 key 管理，默认掩码显示。
- 模型角色：通过 `llm.model_roles` 路由 `chat`、`plan`、`compress`、`fast`。
- Workspace 书签与热切换。
- 会话搜索与切换。
- 配置导入/导出：默认脱敏，with-keys 需二次确认。
- `/doctor` 健康检查面板。
- 更新配置文档、用户手册并扩展测试。
