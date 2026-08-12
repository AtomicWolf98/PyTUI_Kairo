# TUI V2 重建基线（工单 B0 记录）

> 创建日期：2026-08-12
> 依据：《docs/tui-v2-execution-plan.md》工单 B0

## 基线 SHA

- 分支：`main`
- 原始 HEAD：`b8f7ad989d9d65e1e33ad1ea48f94597a5000875`
- 远端：`origin https://github.com/AtomicWolf98/PyTUI_Kairo.git`
- Python：`3.14.0`（仓库 `.venv` 内）
- 依赖：`textual 8.2.7`、`rich 14.3.3`；`kairo-kernel` / `kairo-tui` 未以 wheel 安装，开发环境为源码直跑（`kairo_kernel` 从仓库根导入）

## 工作树保护（B0 步骤 5/6）

用户选择：**纳入基线 checkpoint 提交**。

- Checkpoint 提交：`684a4dbab2493921049d176aed3a03d104f863bf`
  `chore(tui): checkpoint pre-v2 working tree`
- 纳入的未提交修改（共 30 个文件）：
  - 修改：`README.md`、`dist/kairo_kernel-0.4.0a2-py3-none-any.whl`、`dist/kairo_tui-0.4.0a2-py3-none-any.whl`、`docs/commands.md`、`docs/en/index.md`、`docs/en/user-manual.md`、`docs/index.md`、`docs/zh/index.md`、`docs/zh/user-manual.md`、`frontends/tui/README.md`、`frontends/tui/kairo_tui/app.py`、`frontends/tui/kairo_tui/commands.py`、`frontends/tui/kairo_tui/layout.py`、`frontends/tui/kairo_tui/screens/chat.py`、`frontends/tui/kairo_tui/screens/commands.py`、`frontends/tui/kairo_tui/screens/inspector.py`、`frontends/tui/kairo_tui/screens/workspace.py`、`frontends/tui/kairo_tui/widgets.py`、`frontends/tui/pyproject.toml`、`frontends/tui/tests/test_app_layout.py`、`frontends/tui/tests/test_packaging.py`、`frontends/tui/tests/test_wheel_content.py`、`install.bat`、`run.bat`
  - 新增：`docs/tui-redesign.md`、`docs/tui-v2-execution-plan.md`、`frontends/tui/kairo_tui/screens/management.py`、`frontends/tui/tests/test_chat_first_shell.py`、`kairo_exports/session-39bf43b850b34d3993006e35fe76dd99.json`（空 session，无消息、无 secret）、`run-tui.bat`
- 审阅结果：diff 无 secret 标记（`sk-*`、明文 `api_key=` 等零命中）；`kairo_exports/` 中 session JSON 的 `messages` 为空 tuple。

## 截图记录

- 无截图。V2 尚未开发；旧 TUI 的真实终端缺陷留待 P1 人工验收时以截图取证。

## 已确认代码根因（旧 TUI，仅行为取证，不修改）

1. **Composer 被 Setup 门禁禁用**
   `frontends/tui/kairo_tui/app.py:419`
   `composer.disabled = not self.store.state.setup_complete`
   未完成 Setup 时用户无法输入——阻断首要路径。

2. **Setup 表单动态挂载到 Static**
   `frontends/tui/kairo_tui/screens/setup.py:58-93`
   `_mount_form(body: Static)` / `_unmount_form` 把多个输入控件挂到 `#setup-body` Static；无稳定标签、初始焦点与可验证的真实布局。

3. **现有 Pilot 测试不能证明真实可用**
   `frontends/tui/tests/**` 主要断言控件存在、handler 可调用、状态可改变；`test_chat_first_shell.py` 等不覆盖真实键盘焦点、中文输入、按钮文字、终端尺寸。

## pytest 结论（基线时刻）

- 旧 `pytest frontends/tui/tests` 通过**不代表**安装后的真实终端可用：headless 测试发现不了焦点、键盘输入、颜色、按钮文本和终端尺寸问题。
- 本轮完成标准不是“pytest 通过”，而是“安装后的真实终端可由用户只用键盘完成首条消息”。

## 终端环境

- Windows Terminal：`1.24.11911.0`（AppxPackage `Microsoft.WindowsTerminal`）
- cmd.exe：`Microsoft Windows [版本 10.0.26200.8875]`
- 窗口尺寸：基线时刻未测量；P1 人工验收时按 80×24 / 120×30 / 200×50 逐档记录。

## 后续约定

- V2 工单全部在唯一 `main` 分支串行执行，每个工单一个提交。
- 所有 pytest 使用系统临时目录；仓库内不得出现 `.pytest-*`（`.gitignore` 已补充 `.pytest-*/` 与 `**/.pytest-*/`）。
- 后续工单白名单外文件一律不得修改。
