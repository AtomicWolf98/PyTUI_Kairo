# 故障排查

## 启动后进入 plain 模式

Kairo 默认尝试启动 Textual TUI，但在以下情况会 fallback 到 plain：

- 启动时加了 `--plain`；
- `config.json` 中 `ui.mode` 为 `plain`；
- 环境变量 `TERM=dumb`；
- stdin/stdout 不是 TTY（如被管道、重定向、某些 IDE 集成终端）。

强制使用 TUI：

```powershell
kairo --tui
```

若仍失败，检查 `textual` 是否已安装：

```powershell
python -m pip install -e ".[dev]"
```

## `/config` 后出现 `call_from_thread` RuntimeError

该问题在当前版本已修复。根因是同步 Slash Command 在 UI 线程中错误调用了仅允许后台线程使用的 `call_from_thread()`。

确认当前代码包含 UI 线程识别逻辑后运行：

```powershell
python -m pytest tests/test_kairo_ui.py
```

若仍出现旧堆栈，重新执行 editable install：

```powershell
python -m pip install -e ".[dev]"
kairo --help
```

## 输入 `/` 只能看到部分命令

命令菜单视口约显示 7 行，但完整目录都已载入。使用 `Up` / `Down` 或鼠标滚轮继续浏览；输入前缀可过滤。如果第 8 条以后无法选中，运行 `tests/test_kairo_ui.py` 检查是否启动了旧安装。

## Dock 宽度或窄屏底栏不正确

默认宽屏 Dock 为终端宽度的约三分之一，限制在 36 至 64 列；低于 120 列时切换为全宽底栏。旧 `dock_width` 会在加载时迁移为新比例配置。检查本地 `config.json` 的 `dock_width_ratio`、`dock_min_width`、`dock_max_width` 和 `dock_breakpoint`。

## Workspace 没有文件或 Diff

- Git 仓库使用 `git ls-files` 和 `git diff HEAD`；先确认 Git 命令可用。
- 未跟踪文本文件按新增文件显示；二进制文件只显示状态。
- Diff 默认最多读取 200 KB，超限会截断。
- 非 Git 目录只能为已捕获基线的会话改动生成完整 before/after Diff。
- 使用 `/workspace move <path>` 切换到正确目录。

## `/workspace move` 失败

常见原因：

- 路径不存在或不是目录；
- Kairo 对该目录没有写权限（会尝试创建并删除临时测试文件来验证）。

## 工具一直等待确认

当前不是 `yolo` 级别。若被判定为外部/系统/危险操作，即使在 `auto` 级别也会弹窗。确认弹窗中会显示操作分类，如 `[SYSTEM] Execute tool 'run_command'?`。

若不想确认：

- 单次：选择 "Run once"；
- 本次会话剩余操作自动：选择 "Enable AUTO Mode" 或 "Enable YOLO Mode"；
- 长期：在 `config.json` 中设置 `authorization_level`。

## 请求连接失败

1. 用 `/config` 确认活动 provider、model 和 base URL。
2. 确认 provider 的 `api_key_env` 环境变量存在。
3. 检查 provider 是否兼容 `/chat/completions` 流式 SSE。
4. 401/403 通常是凭据问题；context-length 错误会由上下文管理执行一次紧急重试。

## 模型切换后立即接近上下文上限

不同 profile 可配置不同 `context_window`；下一次请求前会自动压缩或裁剪。
