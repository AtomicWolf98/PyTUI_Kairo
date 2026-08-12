# TUI V2 视觉与交互验收（工单 V0）

> 依据：《docs/tui-v2-execution-plan.md》第 18 节
> 自动断言见 `tests_v2/test_size_matrix.py`、`test_focus_contract.py`、`test_visual_snapshots.py`。
> 本文档记录真实终端的人工截图检查结果；禁止只写"看起来正常"。

## 尺寸矩阵（自动测试覆盖）

| 尺寸 | 要求 | 自动测试 |
|---|---|---|
| 60×20 | Composer 可输入；无控件重叠 | ✔ |
| 80×24 | 完整最小体验；modal 可滚动；按钮可见 | ✔ |
| 120×30 | 标准聊天体验 | ✔ |
| 160×40 | 可显示 sidebar | ✔ |
| 200×50 | 表单不拉全宽；内容有最大宽度 | ✔ |

## 视觉断言（自动测试覆盖）

- 每个 Button 的 rendered label 非空 ✔
- focus widget 具有可检测的高亮 class/style ✔
- modal 不超出 screen ✔
- 无水平滚动条（代码块除外）✔
- form label 与 input 同时可见 ✔
- 主要/危险/取消动作样式可区分 ✔
- light/dark 主题前景/背景不相同 ✔
- reduced motion 无 transition ✔

## 人工截图清单（真实终端逐项记录）

执行：

```powershell
cd C:\Users\Admin\Desktop\project\pyTUI
$env:PYTHONPATH = "frontends\tui"
python -m kairo_tui_v2 --config "$env:TEMP\kairo-tui-v2-empty.json"
```

每种尺寸（60×20、80×24、120×30、160×40、200×50）至少截图：

1. 空聊天
2. 输入中文
3. ConnectDialog
4. streaming（配置 provider 后）
5. tool approval
6. command palette
7. workspace sidebar
8. settings

| 截图 | 尺寸 | 检查项 | PASS/FAIL | 问题 |
|---|---|---|---|---|
| 空聊天 |  | 布局、焦点可见 |  |  |
| 中文输入 |  | 不丢字、无错位 |  |  |
| ConnectDialog |  | 标签/按钮/焦点 |  |  |
| streaming |  | 流式渲染、thought 折叠 |  |  |
| tool approval |  | 卡片与按钮可读 |  |  |
| command palette |  | 搜索框焦点 |  |  |
| workspace sidebar |  | 宽度 36–44、无重叠 |  |  |
| settings |  | 内容可读 |  |  |

> 注：V0 提交时真实终端人工截图检查因执行方式（headless）未执行；上述清单留给用户在真实终端补充。自动断言已全部通过。
