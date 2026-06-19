# 开发者指南

本文档面向需要阅读、修改或扩展 Kairo 的开发者。

## 项目结构

```text
kairo.py              CLI 入口：参数解析、TTY 检测、TUI/plain 分发
main.py               兼容入口：python main.py
run.bat               Windows 一键安装启动脚本
pyproject.toml        依赖、入口点、工具配置
agent/
  bootstrap.py        唯一装配入口：Config → Registry → Agent
  commands.py         Slash Command 元数据唯一来源
  config.py           配置加载、保存、环境变量、运行时字段同步
  config_migration.py 旧配置迁移到 llm.providers
  provider_registry.py provider/model 解析归一化
  core.py             Agent facade：命令语义、会话、工具生命周期
  interaction.py      LLM 交互循环：Plan/Auto/Thinking、工具授权、上下文治理
  llm.py              OpenAI-compatible SSE 客户端（流式、重试、代理）
  context_manager.py  会话、token 估算、摘要、安全裁剪
  workspace.py        只读文件扫描、Git/非 Git Diff、会话触达追踪
  tui_widgets.py      plain 模式输入、菜单与 Dock
  input_helper.py     （已删除）
  ui/
    app.py            Textual 应用、事件桥接、快捷键、Workspace 刷新
    widgets.py        可复用组件：Composer、StatusDock、ChoiceModal 等
    mascot.py         Kai 动画帧
    events.py         AgentEvent / EventConsole
tools/
  base.py             BaseTool、ToolRegistry、@skill 装饰器
  policy.py           Permission、OperationScope、WorkspacePathPolicy、授权级别
  file_ops.py         read_file / write_file / list_dir
  patch_ops.py        patch_file / search_file
  shell.py            run_command（持久 Shell）
  web.py              web_fetch
agent/repl.py         Python REPL 与持久 Shell 子进程
skills/               运行时加载的本地自定义工具目录
tests/                单元测试、UI headless 测试、安全测试、评估 harness
```

## 模块职责

| 路径 | 职责 |
| --- | --- |
| `kairo.py` | CLI 参数、TTY 检测、Textual/plain 入口选择 |
| `agent/commands.py` | Slash Command 单一元数据源 |
| `agent/config.py` | JSON、环境变量、llm.providers 与 UI 配置归一化 |
| `agent/bootstrap.py` | 唯一正式装配入口 |
| `agent/core.py` | Agent facade：命令语义、shutdown、workspace 切换回调 |
| `agent/interaction.py` | LLM/工具循环、Plan Mode、授权确认、上下文治理 |
| `agent/llm.py` | OpenAI-compatible 流式 HTTP/SSE 适配 |
| `agent/context_manager.py` | 进程内会话、token 估算、摘要与安全裁剪 |
| `agent/workspace.py` | 只读文件扫描、会话触达追踪、Git/非 Git Diff 快照 |
| `agent/ui/` | Textual 应用、命令菜单、Workspace/Dock、事件桥接和 Kai 动画 |
| `agent/tui_widgets.py` | plain 模式的输入、菜单与 Dock |
| `agent/repl.py` | 持久 Python REPL 与持久 Shell 子进程 |
| `tools/` | 工具接口、注册表和内置工具实现 |
| `skills/` | 运行时加载的本地自定义工具目录 |

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

运行源码：

```powershell
kairo
python kairo.py --plain
```

## 测试

```powershell
# 完整离线测试
python -m pytest

# 聚焦上下文管理
python -m pytest tests/test_context_manager.py

# Textual headless 交互测试
python -m pytest tests/test_kairo_ui.py

# 文件树、Git 和非 Git Diff 状态层
python -m pytest tests/test_workspace.py

# 安全策略与授权级别
python -m pytest tests/test_security.py

# 真实 provider 端到端评估，可能产生费用和文件改动
python tests/harness.py
```

当前完整离线基线为 93 passed。合并前最低要求：完整 pytest 通过、`kairo --help` 可运行、`python kairo.py --plain` 可启动。

## 一次请求的生命周期

1. Composer 提交文本，Slash Command 在 UI 层处理；普通文本交给 Agent worker。
2. Agent 把用户消息加入当前 session，并在请求前计算实际上下文估算。
3. 达到阈值或输出预算不足时，Agent 先摘要压缩，再在必要时按完整轮次裁剪。
4. `LLMClient` 使用 urllib 发起流式请求，解析 content、thought、tool_calls、usage 和 context_error。
5. Textual 路径把连续 delta 合并后刷新当前消息；后台线程只投递事件，不直接操作 widget。
6. 工具调用先由 `InteractionRunner` 根据 `authorization_level` 和 `OperationScope` 决定是否授权，结果完整写入 history。
7. provider 报 context-length 错误时只做一次紧急压缩/裁剪重试。

Workspace 使用独立后台 worker。工具事件携带原始参数、目标路径、scope 和成功状态；UI 只消费不可变快照。

## Textual 线程与事件契约

`EventConsole` 同时被两类调用者使用：Textual 主线程中的同步 Slash Command，以及 Agent worker 中的流式交互。因此 `KairoApp.emit_from_worker()` 必须先判断当前线程：

- 当前线程是 UI 线程：直接 `post_message()` 或 `set_timer()`；
- 当前线程是后台 worker：通过 `call_from_thread()` 回到 UI 线程；
- content/thought delta 先进入带锁缓冲区，再以约 30 FPS 合并刷新；
- 后台线程不得直接查询或修改 Textual widget。

`/config`、`/skills`、`/plan`、`/manual`、`/auto`、`/yolo`、`/think` 等同步命令会在 UI 线程进入 `Agent.handle_command()`。

## 安全模型

- 每个工具调用会被 `classify_scope(arguments)` 归类为 `INTERNAL` / `EXTERNAL` / `SYSTEM` / `DESTRUCTIVE`。
- `is_authorized(level, scope)` 决定是否弹窗：
  - `yolo`：全部通过；
  - `auto`：仅 `INTERNAL` 通过；
  - `manual`：全部需确认。
- 文件类工具通过 `WorkspacePathPolicy` 限制在工作区内；Shell/Python 通过关键字和正则做保守分类。
- 这是语法/策略层沙箱，不是进程级隔离；高敏感场景需要 OS 级隔离。

## 如何新增内置工具

1. 在 `tools/` 中继承 `BaseTool`，提供 `name`、`description`、JSON Schema `parameters` 和 `execute()`。
2. 如需要，覆盖 `classify_scope(arguments) -> OperationScope` 以支持授权系统。
3. 在 `agent/bootstrap.py` 注册实例。
4. 对纯逻辑添加 `tests/test_tools.py` 测试；涉及输出流时使用 `emit_output()`，不要直接写 stdout。
5. 确认工具返回值可序列化为字符串并适合写入模型 history。

运行时自定义 skill 可放在配置的 `skills_dir`。加载器会发现使用 `@skill` 装饰器暴露的函数；不可信 skill 与直接执行本地 Python 等价。

## 如何新增 Slash Command

1. 在 `agent/commands.py` 的 `COMMAND_CATALOG` 中添加条目。
2. 在 `Agent.handle_command()` 中实现语义。
3. 若命令需要修改配置，调用 `config.save()`。
4. 在 TUI 中，若命令属于需要在 UI 线程同步处理的类别，更新 `KairoApp.handle_command()`。
5. 更新 `docs/commands.md`。

## 修改 TUI

- 布局和主题在 `agent/ui/app.py`。
- 可复用组件在 `agent/ui/widgets.py`。
- Kai 的固定单元格帧在 `agent/ui/mascot.py`。
- Agent 到 UI 的线程安全桥在 `agent/ui/events.py`。
- Workspace 数据采集在 `agent/workspace.py`，扫描和 Git 操作必须由 worker 调用。
- 同步 Slash Command 在 UI 线程执行；事件发送必须直接 `post_message()`，不能调用 `call_from_thread()`。
- Agent/Workspace worker 只能通过线程安全事件入口回到 UI，不能直接操作 widget。
- 连续 token 应合并刷新，不能每个 token 创建新组件。
- 新交互应保持 Composer 焦点，并提供 plain 模式对应行为。

修改事件流后至少运行：

```powershell
python -m pytest tests/test_kairo_ui.py
```

## 代码约定

- 默认使用 UTF-8 和类型提示；公开边界写简短 docstring。
- 不提交 `.venv`、缓存、构建产物、日志或本地配置。
- 不把协议解析、UI 渲染和 Agent 决策塞进同一模块。
- 修改行为时补测试；修复回归时先写能复现问题的测试。

## 发布检查

1. 更新 `pyproject.toml` 版本和 `CHANGELOG.md`。
2. 运行完整 pytest 与 CLI 冒烟测试。
3. 使用不含密钥的全新目录验证 `pip install -e .` 和 `kairo --help`。
4. 检查 `git diff --check`、敏感信息和生成物。
5. 在 Windows Terminal 手动验证全屏、窄屏、plain 和 reduced-motion。

## 关键不变量

- session history 第一条必须是主 system instruction。
- tool result 不能脱离对应 assistant tool call。
- 压缩和裁剪按完整用户轮次处理，至少保留当前用户轮次。
- 流式响应期间不重写 history；进入下一次模型请求前再治理上下文。
- Textual widget 只能由 UI 线程更新；UI 线程禁止调用 `call_from_thread()`。
- plain 和 Textual 共用 Agent、配置、会话、工具语义和命令元数据。
- 环境变量中的 API Key 不得由 `Config.save()` 写回磁盘。

## 已知限制与技术债

| 优先级 | 项目 | 影响 |
| --- | --- | --- |
| P0 | 轮换曾经出现在工作树中的 provider API Key | 密钥可能已泄露，代码清理不能使旧密钥恢复安全 |
| P1 | 会话没有磁盘持久化 | 重启后会话、摘要和累计 token 全部丢失 |
| P1 | token 数为 provider-neutral 启发式估算 | 与模型真实 tokenizer 可能有偏差，usage 只能校准最近请求 |
| P1 | Shell/Python 工具的最小沙箱是语法/符号层过滤，不是进程级隔离 | 高敏感场景需要 OS 级隔离 |
| P1 | Workspace 使用低频轮询和 Git 子进程 | 超大仓库可能增加后台 CPU/IO |
| P2 | 尚无 CI、类型检查、lint 和覆盖率门槛 | 质量依赖本地执行 |
| P2 | UI 测试依赖本地已安装 `textual` | 纯最小环境下会自动跳过相关测试 |
| P2 | 主题 CLI 参数尚未形成多主题系统 | 当前主要由内置 Kairo CSS 决定 |
| P3 | 已声明 MIT LICENSE，需确认所有依赖兼容 | 公开分发边界已清晰，但需持续审查新增依赖 |

## 推荐后续里程碑

### 0.2.x 稳定化

- 建立 GitHub Actions：Python 3.10-3.14、pytest、CLI import smoke。
- 对 `llm.providers` 配置做 JSON Schema 校验，启动时明确报告缺失环境变量。
- 为 `tests/harness.py` 增加临时工作目录参数，降低真实 provider 评估风险。
- 增加 macOS/Linux shell smoke，消除 Windows 专用假设。

### 0.3 会话持久化

- 定义版本化 session schema 和原子写入策略。
- 保存 history、summary、usage、profile 名称和时间戳，但绝不保存 API Key。
- 增加恢复、迁移、损坏文件隔离和隐私清理命令。

### 0.4 安全与可扩展性

- 把 provider adapter 与 Agent 编排解耦，支持可测试的 transport。
- 引入更精确的 Shell/Python 静态分析，降低误报。

## 变更交接模板

每次交接至少记录：目标、行为变化、配置迁移、测试证据、已知风险、未完成事项。不要只留下“测试通过”，应注明具体命令、测试数量、终端宽度覆盖和是否调用真实 provider。新增故障及修复方式同步写入 [故障排查](troubleshooting.md)。
