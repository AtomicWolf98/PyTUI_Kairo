# P1 真实 Windows Terminal 人工验收清单

> 工单：P1（ConnectDialog 与真实终端 Gate）
> 依据：《docs/tui-v2-execution-plan.md》第 11 节
> 此 Gate 不能由 pytest 代替。执行者在真实终端逐项记录 PASS/FAIL。

## 0. 准备

1. 构建临时开发入口（V2 尚未接管 console script）：

   ```powershell
   cd C:\Users\Admin\Desktop\project\pyTUI
   $env:PYTHONPATH = "frontends\tui"
   python -m kairo_tui --config "$env:TEMP\kairo-tui-v2-empty.json"
   ```

2. 确认 `%TEMP%\kairo-tui-v2-empty.json` 不存在（空配置 → 空 kernel，无 provider）。
3. 分别打开 **Windows Terminal**（已装 1.24.11911.0）和传统 **cmd.exe** 各执行一次完整清单。
4. 截图保存到本工单交付说明（截图不得包含真实 API key；不提交仓库，除非用户明确要求）。

## 1. 逐项检查（每项记录 PASS / FAIL + 观测）

| # | 检查项 | Windows Terminal | cmd.exe |
|---|---|---|---|
| 1 | 启动后直接输入 `hello 中文 🚀`，字符完整出现在 Composer |  |  |
| 2 | 按 Enter，ConnectDialog 出现 |  |  |
| 3 | 所有字段标签可见（Provider type / Model / Base URL / API key / Context window / Max output tokens / Temperature） |  |  |
| 4 | 所有按钮文字可见（Test connection / Save and send / Save / Cancel） |  |  |
| 5 | Tab 顺序无跳跃、无不可见焦点（Provider → Model → Base URL → API key → Context → Max tokens → Temperature → 4 按钮） |  |  |
| 6 | Escape 关闭 modal，原文本完整恢复且 Composer 重新聚焦 |  |  |
| 7 | 窗口分别调整到 80×24、120×30、200×50；Composer 始终可见 |  |  |
| 8 | 中文输入不丢字（逐字输入 + 粘贴各一次） |  |  |

## 2. 截图清单（每张人工检查后记录）

1. 启动后空聊天（80×24）
2. 输入 `hello 中文 🚀` 后的 Composer
3. ConnectDialog 打开（80×24）
4. ConnectDialog 打开（200×50）
5. Escape 后草稿恢复

## 3. 失败回退条件

任一条件成立即停止后续 Textual 开发，新建决策工单改用
`prompt_toolkit>=3.0,<4` 重做 P0/P1（不得同时保留两套实现）：

- Windows Terminal 或 cmd.exe 无法稳定输入；
- 中文输入丢字；
- 焦点不可见或 Tab 顺序无法稳定控制；
- 80×24 无法保留 Composer；
- 按钮文字空白或主题不可读。

## 4. 结论

- 全部 PASS 后，由用户在真实终端现场确认（Freeze B 前提）。
- 记录结果后，将本文件检查结果回填到本工单交付说明。
