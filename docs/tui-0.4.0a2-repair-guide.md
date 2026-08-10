# Kairo TUI 0.4.0a2 精准修复指南

## 1. 文档目标

本文档用于修复提交 `d753009733e8c09ffa60b9774560e464a670e530` 在本地验收和 GitHub Actions 中暴露的问题，并把 `kairo-kernel 0.4.0a2 / Kernel API 1.1` 与 `kairo-tui 0.4.0a2` 恢复到可以正式验收的状态。

本文档是执行规范，不是重构建议。执行者必须逐项完成、逐项验证，不得自行扩大范围。

当前验收基线：

- 本地分支：仅 `main`。
- 远端分支：仅 `origin/main`。
- 本地与远端当前提交：`d753009733e8c09ffa60b9774560e464a670e530`。
- Kernel 版本：`0.4.0a2`。
- TUI 版本：`0.4.0a2`。
- Kernel API：`1.1`。
- 当前远端失败运行：[GitHub Actions run 31378295219](https://github.com/AtomicWolf98/PyTUI_Kairo/actions/runs/31378295219)。

最终目标不是“失败测试偶尔能通过”，而是：生产竞态被消除、测试不再依赖机器速度、源码与离线 wheel 一致、完整远端矩阵全绿。

## 2. 已确认问题与处理分类

| ID | 优先级 | 类型 | 已确认现象 | 主要定位 |
|---|---:|---|---|---|
| TUI-R1 | P0 | 生产代码竞态 | Workspace 页面卸载后，后台 worker 仍查询已被移除的 `#workspace-changes`，抛出 `textual.css.query.NoMatches`，最终成为 `WorkerFailed` | `frontends/tui/kairo_tui/screens/workspace.py:134,181` |
| TUI-R2 | P0 | 生产代码竞态 | 用户选择“等待任务结束后退出”时，turn 可能在设置 `_exit_when_idle` 前已经结束，之后没有新的 store 更新触发退出 | `frontends/tui/kairo_tui/app.py:286-309` |
| TUI-R3 | P1 | 测试同步缺陷，需验证生产行为 | 后台 turn/session 切换测试依赖 `delay=0.5`；慢机器上 turn 可能在断言前自然结束。该文件的 `_wait_for` 超时后还会静默返回 | `frontends/tui/tests/test_chat_screen.py:191,813` |
| TUI-R4 | P1 | 测试同步缺陷，需验证生产行为 | 新建聊天测试只执行一次 `pilot.pause()` 就断言异步 worker 已完成，Windows 3.11 上可能仍停留在 Setup 页面 | `frontends/tui/tests/test_toggles.py:82`；生产入口 `frontends/tui/kairo_tui/app.py:237-247` |
| CI-R1 | P0 | CI 配置错误 | Alpha wheel job 使用错误的 YAML/Bash 多行环境变量写法，shell 把第二个变量赋值当成命令 | `.github/workflows/ci.yml:54-56` |

除 TUI-R1、TUI-R2 外，不得在尚未获得确定性复现证据时修改生产业务逻辑。TUI-R3、TUI-R4 应先修复测试同步方式；若确定性测试仍失败，再依据事件和状态证据修改 TUI adapter。

## 3. 总体边界

### 3.1 允许修改

仅允许按本指南修改以下路径：

- `frontends/tui/kairo_tui/screens/workspace.py`
- `frontends/tui/kairo_tui/app.py`
- `frontends/tui/tests/test_size_matrix.py`
- `frontends/tui/tests/test_workspace_screen.py`
- `frontends/tui/tests/test_exit_flow.py`
- `frontends/tui/tests/test_chat_screen.py`
- `frontends/tui/tests/test_toggles.py`
- `frontends/tui/tests/support/fakes.py`，仅当需要新增可控 gate provider 时
- `.github/workflows/ci.yml`
- `dist/kairo_tui-0.4.0a2-py3-none-any.whl`，仅在所有源码门禁通过后的发布重建阶段更新
- `dist/kairo_kernel-0.4.0a2-py3-none-any.whl`，仅在发布检查脚本要求成对重建时更新
- 本文档，若实际实现与步骤产生必要偏差，只能补充事实和最终验证结果

### 3.2 严禁修改

- `kairo_kernel/**`：本轮问题不要求修改 Kernel、Kernel API、DTO、事件结构或存储语义。
- `agent/**`、`kairo.py`：不得恢复旧 Textual 前端或改变兼容入口。
- `frontends/tui/kairo_tui/store.py`、`event_pump.py`：除非完成确定性复现并证明问题来自 reducer 或 replay；若无证据不得修改。
- 版本号、依赖范围、Python 支持矩阵、包名、console script。
- TUI 布局、CSS、页面结构、快捷键、导航编号、配色和交互文案。
- Session、Workspace、shutdown、cancel 的 Kernel 语义。
- Git 分支结构。整个修复只允许在 `main` 上进行，不创建临时分支或 worktree。

### 3.3 禁止采用的伪修复

以下做法一律不接受：

- 增大 `sleep`、`pilot.pause()` 次数或全局测试超时来掩盖竞态。
- 给失败测试添加 `skip`、`xfail`、平台判断或重试插件。
- 给 CI 添加 `continue-on-error`。
- 用宽泛的 `except Exception: pass` 吞掉 worker 错误。
- 只在 `query_one()` 外包一层异常捕获，却不取消页面所属 worker。
- 因测试失败而禁止 session 并发、切换 session 时取消后台 turn，或改变“等待退出”的既有语义。
- 直接修改或删除已提交 wheel，而不从通过门禁的源码重新构建。

## 4. 执行顺序

必须严格按以下顺序串行执行：

1. 修复 Workspace 生命周期竞态并加入确定性回归测试。
2. 修复等待退出竞态并加入“turn 在点击前结束”和“点击后结束”两类测试。
3. 把 session 切换测试改为 gate 驱动，并让等待工具在超时时明确失败。
4. 把新建聊天测试改为等待可观察状态，不修改其业务断言。
5. 修复 Alpha wheel CI 命令。
6. 运行定向重复门禁。
7. 运行 Kernel、TUI、静态检查和发布检查全套门禁。
8. 重新构建离线 wheel，验证安装和 smoke。
9. 提交到唯一的 `main`，推送后检查完整 GitHub Actions 矩阵。

前一阶段未通过，不得进入后一阶段。

## 5. TUI-R1：Workspace 页面 worker 生命周期竞态

### 5.1 根因

`WorkspaceScreen.on_mount()` 当前直接启动 `_load_all()`：

```python
self.run_worker(self._load_all())
```

`_load_all()` 连续等待 tree、bookmark、changed-files 三次异步读取。页面切换会移除 `WorkspaceScreen`，但 worker 没有被记录和取消。`_fetch_changes()` 在一次 `await` 返回后执行：

```python
if not self.is_mounted:
    return
container = self.query_one("#workspace-changes", VerticalScroll)
```

`is_mounted` 检查与 `query_one()` 不是原子操作；页面可以在两者之间被卸载。因此 `query_one()` 仍可抛出 `NoMatches`。远端 Windows 3.12 的四个尺寸用例均出现过该堆栈，本地也可重复。

### 5.2 必须实施的代码改动

在 `WorkspaceScreen` 内建立“页面拥有 worker、卸载统一取消”的生命周期规则：

1. 从 `textual.worker` 导入 `Worker`。
2. 在 `__init__` 创建专属于当前 screen 实例的 worker 容器，例如 `self._workers: list[Worker[None]] = []`。
3. 新增 `_start_worker(coroutine)` 小函数：只能调用 `self.run_worker(...)`、保存返回的 `Worker` 并返回该 handle，不得包含业务逻辑。
4. 将 `WorkspaceScreen` 内所有 `self.run_worker(...)` 替换为 `_start_worker(...)`，至少覆盖：
   - 初始 `_load_all()`；
   - bookmark 保存/删除；
   - workspace move；
   - changed-file diff；
   - preview。
5. 新增同步 `on_unmount()`：遍历该 screen 保存的 worker；对仍在运行的 worker 调用 `cancel()`；最后清空容器。
6. `asyncio.CancelledError` 必须自然向上传播，不得在 workspace worker 中转换成错误 notice。
7. 所有发生在 `await` 之后的 DOM 查找使用 `query_one_optional()`；返回 `None` 时立即退出当前渲染函数。
8. 对同一 widget 的多步异步 DOM 修改，在每个可能让出控制权的操作后重新确认 screen 仍挂载且 widget 仍属于当前 screen。worker 取消是主保证，optional query 是最后一道竞态保护，两者缺一不可。
9. 不修改 workspace revision、stale-drop、move transaction、recent workspace 或 Kernel 调用顺序。

### 5.3 必须新增的测试

在 `test_workspace_screen.py` 增加：

- `test_unmount_cancels_pending_workspace_load`
  - 用 `asyncio.Event` gate 阻塞 `changed_files()`；
  - 打开 Workspace，并确认请求已经开始；
  - 在请求完成前切换到 Chat；
  - 释放 gate；
  - 退出 `run_test()`；
  - 整个过程不得产生 `WorkerFailed` 或 `NoMatches`。

- `test_rapid_workspace_navigation_drops_detached_results`
  - 在 Workspace 与另外一个页面之间快速切换至少 20 次；
  - workspace read 必须被 gate 控制，保证存在“结果返回时旧页面已卸载”的情况；
  - 最终页面只能有一个实例；
  - 不得出现后台 worker exception。

在 `test_size_matrix.py` 保留四种尺寸原有断言，不得降低覆盖。新增或调整测试时，必须让每一次页面切换等待“目标 screen 已挂载”，而不是只等待固定时间。

### 5.4 完成判据

- `workspace.py` 中不存在未纳入 screen 生命周期管理的 `self.run_worker(...)`。
- 页面卸载后，旧 screen 的异步结果不能再修改 DOM。
- 四种尺寸连续运行 20 轮无失败。
- 不能通过吞异常达成通过。

## 6. TUI-R2：等待任务完成后退出的竞态

### 6.1 根因

当前流程为：

1. `request_exit()` 从 Kernel 获取 active turns；
2. 显示退出 modal；
3. 用户选择 `exit-wait`；
4. 设置 `_exit_when_idle = True`；
5. 等待后续 store 更新触发 `_on_store_changed()`；
6. 当 `state.active_turns` 为空时再 shutdown。

如果 turn 在步骤 2 与步骤 4 之间结束，terminal event 已经被 store 消费，步骤 4 之后不会再有保证触发的状态变化，应用会永久等待。macOS 3.14 已出现 `test_exit_wait_completes_after_turn_finishes` 超时。

### 6.2 必须实施的代码改动

退出等待必须直接依赖 Kernel 的公共 `wait(turn_id)`，不得把未来的 store callback 当作唯一完成信号：

1. 在 `request_exit()` 打开 modal 前保存本次 active turn ID 快照。
2. 用户选择 `exit-wait` 后，对快照中的每个 turn 调用公开的 `self.kernel.wait(turn_id)`。
3. 可使用 `asyncio.gather` 并行等待多个 session 的 turn；不得轮询 UI store。
4. 所有 wait 返回后，再调用 `self.kernel.active_turns()` 复核。
5. 若仍存在 active turn：保持应用运行并显示明确错误，不得强制退出或偷偷 cancel。
6. 若为空：调用现有 `_shutdown_and_exit()`。
7. 删除 `_exit_when_idle` 字段以及 `_on_store_changed()` 中对应的自动退出分支，避免保留两套退出状态机。
8. `exit-stop` 保持 `cancel_active_turn=True`；`exit-back` 保持不改变任何 turn；不得改变这两条路径。

该实现必须覆盖 turn 在以下任意时刻结束的情况：modal 显示前、modal 显示后但点击前、点击后等待中。

### 6.3 必须新增或改写的测试

不要继续使用 `delay=0.2` 制造时间窗口。新增受两个 `asyncio.Event` 控制的 provider：`started` 表示 stream 已开始，`release` 控制何时完成。

测试必须覆盖：

- `test_exit_wait_when_turn_finishes_after_choice`
  - turn 已 started；
  - 打开 modal；
  - 点击 `exit-wait`；
  - 确认应用尚未退出；
  - release provider；
  - 等待 Kernel 进入 `stopped`。

- `test_exit_wait_when_turn_finishes_before_choice`
  - turn 已 started；
  - 打开 modal；
  - release provider，并确认 Kernel active turns 已空；
  - 再点击 `exit-wait`；
  - 应用仍必须完成 shutdown，不得等待下一条 store event。

- 保留并复核 `exit-stop`、`exit-back` 和无 active turn 直接退出测试。

### 6.4 完成判据

- `app.py` 不再包含 `_exit_when_idle`。
- “等待退出”只等待，不取消 turn。
- turn 结束与 modal 点击的顺序不影响退出结果。
- 不增加任意固定 sleep。

## 7. TUI-R3：后台 turn/session 切换测试确定性

### 7.1 当前问题

`test_switching_sessions_does_not_cancel_background_turn` 使用 `delay=0.5` 假设 turn 在点击 session chip 后仍运行。不同 OS 和 Python 版本的 Textual 调度速度不同，0.5 秒不是同步契约。

同一文件的 `_wait_for()` 在达到轮询上限后静默返回，使后续断言在错误位置失败，丢失真正的超时条件。

### 7.2 必须实施的测试改动

1. 修改 `test_chat_screen.py` 的 `_wait_for()`：达到上限必须抛出 `AssertionError`；错误消息必须说明等待的条件，建议增加 `description` 参数。
2. 将该测试的 provider 改为 gate provider：
   - stream 开始时设置 `started`；
   - 在 `release` 被设置前不产生 terminal event；
   - cancellation token 仍必须被观察，避免测试 teardown 悬挂。
3. 精确测试顺序：
   - 创建 session A、B；
   - A 提交 turn；
   - 等待 provider `started` 和 Kernel active turn 中出现该 `turn_id`；
   - 切换到 B；
   - 同时断言 TUI active session 是 B、Kernel active turn 仍属于 A；
   - 不得调用 `kernel.cancel()`；
   - release provider；
   - 按该 `turn_id` 等待 terminal 状态 `succeeded`；
   - 最终 Kernel active turns 为空。
4. 断言必须按具体 `turn_id`，不得只用 `"succeeded" in statuses.values()`。

### 7.3 生产代码修改条件

完成上述确定性测试后：

- 若测试通过：不修改生产代码，该问题归类为测试同步缺陷。
- 若测试仍失败：先记录 Kernel `active_turns()`、store `active_turns`、terminal event sequence 和是否出现 cancel 调用；只有证据指向 TUI reducer/event pump 时，才允许提出额外变更。
- 无论如何，不得通过切换 session 时取消 A 的 turn 来修复。

## 8. TUI-R4：新建聊天测试确定性

### 8.1 当前问题

`action_new_chat()` 通过 `run_worker(self._new_chat())` 异步创建 session。测试按下 `Ctrl+N` 后只调用一次 `pilot.pause()`，不能保证 SQLite/session list 和 store dispatch 已完成。

### 8.2 必须实施的测试改动

1. 测试按下 `Ctrl+N` 后，等待以下三个可观察条件同时成立：
   - `active_session_id is not None`；
   - `state.page is PageId.CHAT`；
   - `#chat-screen` 已挂载。
2. 等待辅助函数超时必须抛出含条件名称的 `AssertionError`。
3. 保留原测试使用空配置的事实；该用例验证的是“新建 session 并导航”，不允许顺便修改 setup/config。
4. 增加一条断言：当 `setup_complete` 仍为 `False` 时 composer 继续 disabled，避免新建聊天绕过 setup gate。

### 8.3 生产代码修改条件

应用 `_new_chat()` 当前已经按 `create -> SessionAction -> refresh_sessions -> PageAction(CHAT) -> _show_page(CHAT)` 顺序执行。仅当确定性等待仍失败，才允许修改该方法；不得增加 sleep，也不得绕过 session create 结果检查。

## 9. CI-R1：Alpha wheel 环境变量语法

将 `.github/workflows/ci.yml` 中以下错误写法：

```yaml
- run: KAIRO_TUI_WHEEL=dist/kairo_tui-0.4.0a2-py3-none-any.whl \
       KAIRO_KERNEL_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl \
       python -m pytest frontends/tui/tests/test_wheel_content.py
```

替换为 GitHub Actions 原生 `env` 映射：

```yaml
- name: Validate TUI wheel contents
  env:
    KAIRO_TUI_WHEEL: dist/kairo_tui-0.4.0a2-py3-none-any.whl
    KAIRO_KERNEL_WHEEL: dist/kairo_kernel-0.4.0a2-py3-none-any.whl
  run: python -m pytest frontends/tui/tests/test_wheel_content.py
```

不得改成单行 shell-specific 语法，因为该 job 将来可能迁移 runner；`env` 是平台无关且语义明确的写法。

完成后检查：

- YAML 缩进正确。
- 两个变量只作用于该 step。
- wheel 文件名仍与 `0.4.0a2` 完全一致。
- 不修改 build、twine、pip-audit 等其他发布步骤。

## 10. 定向验证命令

所有命令均从仓库根目录执行。PowerShell 中先设置本次进程使用的 TUI 源码路径：

```powershell
$env:PYTHONPATH = (Resolve-Path "frontends/tui").Path
```

### 10.1 单次定向测试

```powershell
python -m pytest frontends/tui/tests/test_workspace_screen.py -q
python -m pytest frontends/tui/tests/test_size_matrix.py -q
python -m pytest frontends/tui/tests/test_exit_flow.py -q
python -m pytest frontends/tui/tests/test_chat_screen.py -q
python -m pytest frontends/tui/tests/test_toggles.py -q
```

### 10.2 竞态重复测试

以下测试必须连续 20 轮通过，任意一轮失败即停止：

```powershell
1..20 | ForEach-Object {
    python -m pytest `
        frontends/tui/tests/test_size_matrix.py `
        frontends/tui/tests/test_workspace_screen.py `
        frontends/tui/tests/test_exit_flow.py `
        frontends/tui/tests/test_chat_screen.py::test_switching_sessions_does_not_cancel_background_turn `
        frontends/tui/tests/test_toggles.py::test_new_chat_creates_session_and_opens_chat `
        -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

不得用 pytest retry 插件代替这一步。

## 11. 全量质量门禁

### 11.1 Kernel 不回归

```powershell
python -m pytest tests/kernel -q
python -m ruff check kairo_kernel tests/kernel
python -m mypy kairo_kernel
```

预期：Kernel 原有测试全部通过；Windows 无 symlink 权限导致的既有 skip 可以保留，不得新增 skip。

### 11.2 TUI 全量

```powershell
python -m pytest frontends/tui/tests -q
Push-Location frontends/tui
python -m ruff check kairo_tui tests
python -m mypy kairo_tui
Pop-Location
```

要求：

- 零 failed、零 error。
- 不新增 warning 白名单。
- Ruff 与 Mypy 零错误。
- TUI 边界测试继续证明前端只使用公开 Kernel surface。

### 11.3 兼容入口与源码 smoke

```powershell
python -m pytest tests/test_tui_cutover.py tests/test_release_metadata.py -q
python -m kairo_tui.smoke
```

预期 smoke 输出包含 `KAIRO_TUI_SMOKE_OK`。

## 12. Wheel 重建与离线验收

只有第 10、11 节全部通过后才能重建 wheel。

1. 清理的目标只能是仓库 `dist/` 中本版本的两个 wheel；删除前核对绝对路径，禁止递归删除仓库或用户目录。
2. 从当前通过门禁的源码构建：

```powershell
python -m build
python -m build frontends/tui --outdir dist
python -m twine check `
    dist/kairo_kernel-0.4.0a2-py3-none-any.whl `
    dist/kairo_tui-0.4.0a2-py3-none-any.whl
```

3. 执行仓库发布一致性检查：

```powershell
python tools/release_check.py `
    --wheel dist/kairo_kernel-0.4.0a2-py3-none-any.whl `
    --wheel dist/kairo_tui-0.4.0a2-py3-none-any.whl
```

4. 执行 wheel 内容测试：

```powershell
$env:KAIRO_WHEEL = "dist/kairo_kernel-0.4.0a2-py3-none-any.whl"
python -m pytest tests/kernel/packaging -q
$env:KAIRO_TUI_WHEEL = "dist/kairo_tui-0.4.0a2-py3-none-any.whl"
$env:KAIRO_KERNEL_WHEEL = "dist/kairo_kernel-0.4.0a2-py3-none-any.whl"
python -m pytest frontends/tui/tests/test_wheel_content.py -q
```

5. 创建临时虚拟环境，使用 `--no-index --no-deps` 安装两个本地 wheel，验证：

- `kairo_kernel.__version__ == "0.4.0a2"`；
- `kairo_tui.__version__ == "0.4.0a2"`；
- `KERNEL_API_VERSION == "1.1"`；
- `python -m kairo_tui.smoke` 输出 `KAIRO_TUI_SMOKE_OK`。

6. 记录两个新 wheel 的文件大小和 SHA256。不得继续沿用修复前的旧哈希。

## 13. Git 与远端验收

### 13.1 提交前

```powershell
git status --short --branch
git diff --check
git diff --stat
git branch --format="%(refname:short)"
git worktree list --porcelain
```

必须满足：

- 当前分支为 `main`。
- 没有其他本地分支或额外 worktree。
- 变更只在第 3.1 节 allowlist 内。
- 没有 pytest cache、临时数据库、临时配置或虚拟环境被暂存。

建议使用一个语义明确的源码/CI 提交；如果 wheel 必须单独提交，两个提交仍只能位于同一个 `main`：

```text
fix(tui): harden async lifecycle and release gates
build(win): refresh 0.4.0a2 offline wheels after TUI fixes
```

### 13.2 推送后

推送 `main` 后检查完整 GitHub Actions。验收要求：

- Linux、Windows、macOS 上 Python 3.11、3.12、3.13、3.14 的所有矩阵 job 通过。
- `Alpha wheels (kernel + TUI)` job 通过。
- 没有 cancelled、neutral、skipped 或 `continue-on-error` 掩盖的失败。
- 远端 `main` SHA 与本地 `HEAD` 完全一致。
- 远端仍只有 `main` 一个 branch。

若远端任意 job 失败，本轮仍判定为未完成；不得仅凭本地通过宣布验收。

## 14. 最终交付清单

执行者必须在交付说明中逐项给出：

1. 最终提交 SHA。
2. 实际修改文件清单。
3. TUI-R1、TUI-R2 的根因与修复机制各一句。
4. TUI-R3、TUI-R4 最终被证明是测试问题还是生产问题，以及对应证据。
5. 定向测试 20 轮结果。
6. Kernel、TUI、Ruff、Mypy、cutover、smoke 的实际通过数量。
7. 两个 wheel 的新文件大小和 SHA256。
8. GitHub Actions 运行链接和所有 job 全绿的截图或文本摘要。
9. 本地 `HEAD`、远端 `main` SHA 以及远端 branch 数量。
10. 已知限制；如果没有，明确写“无新增已知限制”。

缺少任何一项，均不能判定为完整交付。

## 15. 完成定义

只有同时满足以下条件，才能宣布 Kairo Kernel 与新 TUI `0.4.0a2` 对齐完成：

- Workspace 页面卸载后零 DOM 写入、零遗留 worker exception。
- “等待退出”对 turn 完成时序不敏感，并且绝不隐式 cancel。
- session 切换不会取消后台 turn，测试不依赖 wall-clock delay。
- 新建聊天测试等待真实状态转换，不依赖单次 event-loop pause。
- Alpha wheel CI 使用 step-level `env` 并通过。
- 本地完整测试、Ruff、Mypy、wheel 安装和 smoke 全部通过。
- GitHub 全矩阵全绿。
- 源码、提交的 wheel、本地 `main`、远端 `main` 均指向同一轮修复成果。
- 本地和远端仍只有一个 `main` branch。
