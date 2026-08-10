# Kairo TUI 0.4.0a2 残余 CI 问题修复指南

## 1. 适用基线与目标

本文档只处理提交 `fc66d28a0898962fca184c52808fbbdd34847b47` 完成第一轮修复后仍然存在的验收阻塞，不重复执行 `docs/tui-0.4.0a2-repair-guide.md` 已经完成的 Workspace 和退出流程修复。

当前基线：

- 本地分支：`main`。
- 远端分支：`main`。
- 本地 `HEAD`、`origin/main`、真实远端 `main`：`fc66d28a0898962fca184c52808fbbdd34847b47`。
- Kernel：`0.4.0a2`，Kernel API `1.1`。
- TUI：`0.4.0a2`。
- 当前失败运行：[GitHub Actions run 31387913638](https://github.com/AtomicWolf98/PyTUI_Kairo/actions/runs/31387913638)。

本轮目标：

1. 消除 `test_two_sessions_run_in_parallel` 的 session-chip 异步重建竞态。
2. 让测试用 `GatedProvider` 不泄漏 pending asyncio task，并保证并发请求各自绑定正确的响应脚本。
3. 让 Alpha wheel job 在干净 GitHub runner 上具备 TUI 运行依赖。
4. 清理既有 Markdown EOF 空白错误。
5. 在不修改 Kernel、版本号和现有 wheel 的前提下，让完整 GitHub Actions 13 个 job 全部成功。

## 2. 当前证据

### 2.1 已通过项目

- Kernel 全量：`318 passed`。
- TUI 全量：`250 passed`。
- Kernel Ruff、Mypy：通过，Mypy 覆盖 86 个源码文件。
- TUI Ruff、Mypy：通过，Mypy 覆盖 37 个源码文件。
- 定向竞态回归：20 轮，每轮 12 项，共 240 项通过。
- 提交 wheel 内容测试：`2 passed`。
- `twine check`：两个 wheel 均通过。
- `tools/release_check.py`：源码与 wheel payload 一致。

### 2.2 未通过项目

当前 GitHub Actions 共 13 个 job：10 个成功，3 个失败。

| Job | 失败步骤 | 已知边界 |
|---|---|---|
| Ubuntu Python 3.13 | `python -m pytest frontends/tui/tests` | 完整错误日志需要有效 GitHub 登录；本地已独立复现 session-chip `NoMatches` 竞态 |
| Ubuntu Python 3.14 | `python -m pytest frontends/tui/tests` | 同上 |
| Alpha wheels | `Validate TUI wheel contents` | Alpha job 未安装 TUI 依赖，wheel smoke 又使用 `--no-deps` |

本地冷启动定向测试曾出现：

```text
test_two_sessions_run_in_parallel
textual.css.query.NoMatches:
No nodes match '#session-<session_id>'
```

失败位置：`frontends/tui/tests/test_chat_screen.py:791`。完整 TUI 套件及后续 20 轮通过不能否定该竞态；只要真实发生过一次，就必须修复。

## 3. 修改边界

### 3.1 允许修改

仅允许修改：

- `.github/workflows/ci.yml`
- `frontends/tui/tests/support/fakes.py`
- `frontends/tui/tests/test_chat_screen.py`
- `docs/tui-0.4.0a2-repair-guide.md`，仅删除 EOF 多余空行
- 本文档，仅补充最终验证结果

### 3.2 禁止修改

- `kairo_kernel/**`
- `frontends/tui/kairo_tui/**`
- `agent/**`
- `kairo.py`
- `pyproject.toml`
- `frontends/tui/pyproject.toml`
- `dist/*.whl`
- Kernel/TUI 版本号和 Kernel API 版本
- Session、turn、event、workspace、shutdown 的业务语义
- 测试数量、CI OS/Python 矩阵和现有质量检查步骤

本轮只修测试基础设施和 CI 安装环境，没有可发布包源码变化，因此严禁重建或修改两个已提交 wheel。

### 3.3 禁止伪修复

- 不得增加固定 `sleep`。
- 不得仅扩大 `_wait_for` 的 polls 或 timeout。
- 不得添加 retry、`skip`、`xfail`、平台判断或 `continue-on-error`。
- 不得删除 Linux/Python 3.13 或 3.14 矩阵。
- 不得捕获并忽略 `NoMatches`。
- 不得通过取消后台 turn、禁用 session 并发或删除内容归属断言来让测试通过。
- 不得让 wheel smoke 临时联网解析依赖来掩盖 CI 父环境未按步骤准备完整的问题。

## 4. 修复 A：稳定 session-chip 测试交互

### 4.1 根因

`ChatScreen._rebuild_chips()` 的行为是：

1. `await strip.remove_children()`；
2. 逐个 `await strip.mount(Button(...))`。

turn terminal event 会改变 chip signature 并触发上述异步重建。`test_two_sessions_run_in_parallel` 在等待两个 turn 成功后立即执行：

```python
await pilot.click(f"#session-{session_a}")
```

如果点击发生在步骤 1 和步骤 2 之间，目标 chip 暂时不存在，`pilot.click()` 内部 `query_one()` 抛出 `NoMatches`。

本轮不修改生产 UI；测试必须等待它实际要操作的 widget 已挂载，这是 Textual UI 测试的正确同步边界。

### 4.2 必须修改的测试代码

在 `test_two_sessions_run_in_parallel` 中，session A 和 session B 的两次点击前都加入显式可观察状态等待。

新增局部辅助函数，或直接使用已有 `_wait_for`：

```python
async def _wait_for_session_chip(pilot, app, session_id: str) -> None:
    await _wait_for(
        pilot,
        lambda: app.query_one_optional(f"#session-{session_id}", Button) is not None,
        description=f"session chip {session_id} mounted",
    )
```

调用顺序必须是：

```python
await _wait_for_session_chip(pilot, app, session_a)
await pilot.click(f"#session-{session_a}")
await _wait_for(
    pilot,
    lambda: app.store.state.active_session_id == session_a,
    description="session A became active",
)
```

session B 使用完全相同的顺序。

点击后的断言必须继续保留：

- A 页面只显示 `A says hi`。
- B 页面只显示 `B says yo`。
- `app.store.state.messages` 总数为 2。
- 两个 turn 均为 `succeeded`。
- 最终 Kernel active turns 为空。

### 4.3 不允许的替代方案

- 不得在 `pilot.click()` 外捕获 `NoMatches` 后重试。
- 不得删除 session 内容文本断言。
- 不得把 UI 点击替换成直接 `SessionAction` dispatch；测试目标包含真实 chip 交互。
- 不得在 turn 完成后固定 `pilot.pause(0.5)`。

## 5. 修复 B：重写 GatedProvider 的请求绑定与 task 清理

### 5.1 当前缺陷

`frontends/tui/tests/support/fakes.py:126-129` 当前创建：

```python
release_task = asyncio.ensure_future(self.release.wait())
cancel_task = asyncio.ensure_future(cancellation.wait())
done, _ = await asyncio.wait(...)
```

存在两个问题：

1. `asyncio.wait()` 返回的 pending task 没有取消、没有 await 回收。
2. response script 在 release 之后才执行 `self.scripts.pop(0)`；两个并发 stream 同时醒来时，脚本归属取决于调度顺序，A/B 内容可能互换。

### 5.2 必须采用的结构

覆盖 `GatedProvider.stream()`，在同步创建 async iterator 时就为该请求保留 script，然后交给单独的 `_gated_stream()`。

目标结构：

```python
def stream(
    self,
    request: ProviderRequest,
    cancellation: CancellationToken,
) -> AsyncIterator[ProviderStreamEvent]:
    self.requests.append(request)
    script = self.scripts.pop(0) if self.scripts else ()
    return self._gated_stream(script, cancellation)
```

`_gated_stream()` 必须：

1. 设置 `started`。
2. 使用 `asyncio.create_task()` 创建 release/cancel task。
3. `await asyncio.wait(..., return_when=asyncio.FIRST_COMPLETED)`。
4. cancellation 先完成时，不产生 provider completion event。
5. release 先完成时，只发送已经绑定给当前请求的 `script`。
6. 无论正常、取消还是异常，都在 `finally` 中取消未完成 task。
7. 使用 `await asyncio.gather(*tasks, return_exceptions=True)` 回收两个 task。

参考结构：

```python
async def _gated_stream(
    self,
    script: tuple[ProviderStreamEvent, ...],
    cancellation: CancellationToken,
) -> AsyncIterator[ProviderStreamEvent]:
    self.started.set()
    release_task = asyncio.create_task(self.release.wait())
    cancel_task = asyncio.create_task(cancellation.wait())
    tasks = (release_task, cancel_task)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task in done and release_task not in done:
            return
        for event in script:
            await asyncio.sleep(0)
            yield event
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

这里必须使用 task 是否属于 `done` 集合判断完成状态，不得调用不存在的 `Task.is_set()`。

### 5.3 必须新增的 fake 单元测试

在 `test_chat_screen.py` 或新的既有测试模块中覆盖：

- `test_gated_provider_reserves_script_per_request`
  - 创建 A/B 两套脚本；
  - 创建两个 stream iterator；
  - 以与创建顺序相反的顺序推进 iterator；
  - A iterator 仍只能得到 A script，B iterator 仍只能得到 B script。

- `test_gated_provider_cancellation_reaps_wait_tasks`
  - stream 已 started、release 未设置；
  - 触发 cancellation；
  - iterator 正常结束且不产生 completed event；
  - 当前 loop 中不残留由该 provider 创建的 release/cancel task。

测试不得依赖访问 asyncio 私有属性。可以让 fake 暴露只读的 active-wait-task 计数用于断言；该计数仅属于测试基础设施。

## 6. 修复 C：补齐 Alpha wheel job 的 TUI 环境

### 6.1 根因

矩阵 job 同时安装：

```yaml
python -m pip install -e ".[dev]"
python -m pip install -e "frontends/tui[dev]"
```

Alpha wheel job 目前只安装根项目：

```yaml
python -m pip install -e ".[dev]"
```

但 `test_wheel_content.py` 创建 `system_site_packages=True` venv，并以 `--no-deps` 安装 Kernel/TUI wheel。干净 Alpha runner 的父环境中没有 TUI 的以下运行依赖：

- `textual>=8.2,<9`
- `rich>=14,<15`
- `keyring>=25,<26`
- `platformdirs>=4,<5`

因此 wheel 本身可以构建、Twine 可以通过，但 headless TUI smoke 无法在该环境导入运行。

### 6.2 唯一允许的 CI 修改

在 Alpha wheel job 的根项目安装步骤之后，增加：

```yaml
- run: python -m pip install -e "frontends/tui[dev]"
```

最终相关顺序必须是：

```yaml
- run: python -m pip install --upgrade pip
- run: python -m pip install -e ".[dev]"
- run: python -m pip install -e "frontends/tui[dev]"
- run: python -m build
- run: python -m build frontends/tui --outdir dist
- run: python -m twine check dist/*.whl
```

必须保留当前正确的 step-level `env`：

```yaml
- name: Validate TUI wheel contents
  env:
    KAIRO_TUI_WHEEL: dist/kairo_tui-0.4.0a2-py3-none-any.whl
    KAIRO_KERNEL_WHEEL: dist/kairo_kernel-0.4.0a2-py3-none-any.whl
  run: python -m pytest frontends/tui/tests/test_wheel_content.py
```

不得把 `--no-deps` 改成静默联网安装依赖；该测试验证的是两个本地 wheel 可在已准备好的离线运行环境中安装和启动。

## 7. 修复 D：清理 Markdown whitespace

运行：

```powershell
git diff --check d753009..HEAD
```

当前报告：

```text
docs/tui-0.4.0a2-repair-guide.md:485: new blank line at EOF.
```

只删除文件末尾多余空白行，保留恰好一个 POSIX newline。不得改写文档正文。

修复后以下两条都必须无输出并返回 0：

```powershell
git diff --check
git diff --check d753009..HEAD
```

## 8. 定向验证

从仓库根目录执行：

```powershell
$env:PYTHONPATH = (Resolve-Path "frontends/tui").Path
```

### 8.1 单次定向测试

```powershell
python -m pytest `
    frontends/tui/tests/test_chat_screen.py::test_two_sessions_run_in_parallel `
    frontends/tui/tests/test_chat_screen.py::test_switching_sessions_does_not_cancel_background_turn `
    frontends/tui/tests/test_exit_flow.py::test_exit_wait_when_turn_finishes_after_choice `
    frontends/tui/tests/test_exit_flow.py::test_exit_wait_when_turn_finishes_before_choice `
    -q
```

### 8.2 50 轮 session 并发稳定性测试

本轮对残余问题采用 50 轮，而不是上一轮的 20 轮：

```powershell
1..50 | ForEach-Object {
    python -m pytest `
        frontends/tui/tests/test_chat_screen.py::test_two_sessions_run_in_parallel `
        frontends/tui/tests/test_chat_screen.py::test_switching_sessions_does_not_cancel_background_turn `
        -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

要求：100 次测试全部通过，任意一次失败即判定未修复。

## 9. 全量本地门禁

### 9.1 Kernel

```powershell
python -m pytest tests/kernel -q
python -m ruff check kairo_kernel tests/kernel
python -m mypy kairo_kernel
```

### 9.2 TUI

```powershell
python -m pytest frontends/tui/tests -q
Push-Location frontends/tui
python -m ruff check kairo_tui tests
python -m mypy kairo_tui
Pop-Location
```

### 9.3 提交 wheel 回归

本轮不得重建 wheel，只验证现有提交 wheel：

```powershell
$env:KAIRO_TUI_WHEEL = "dist/kairo_tui-0.4.0a2-py3-none-any.whl"
$env:KAIRO_KERNEL_WHEEL = "dist/kairo_kernel-0.4.0a2-py3-none-any.whl"
python -m pytest frontends/tui/tests/test_wheel_content.py -q
python -m twine check `
    dist/kairo_kernel-0.4.0a2-py3-none-any.whl `
    dist/kairo_tui-0.4.0a2-py3-none-any.whl
python tools/release_check.py `
    --wheel dist/kairo_kernel-0.4.0a2-py3-none-any.whl `
    --wheel dist/kairo_tui-0.4.0a2-py3-none-any.whl
```

现有 wheel SHA256 必须保持不变：

- Kernel：`E56018DF2C6E1BFB4D6FE40AAD6B4E301C1D3AB4081DCAA18BD1425BE5547B4D`
- TUI：`F19AED1FC09B510F399D6D34124C848624CB34B16F9E743A11B202C1B1C58C26`

若哈希改变，说明执行者越界修改或重建了 wheel，本轮不得提交。

## 10. 提交与远端验收

### 10.1 提交前检查

```powershell
git status --short --branch
git diff --check
git diff --check d753009..HEAD
git diff --name-only fc66d28..HEAD
git branch --format="%(refname:short)"
git worktree list --porcelain
```

必须满足：

- 仅有 `main`。
- 仅有根 worktree。
- 没有 `dist` 变化。
- 变更文件只在第 3.1 节 allowlist 内。
- 没有 pytest cache、临时 venv、SQLite、配置或日志文件。

建议提交信息：

```text
fix(ci): stabilize TUI concurrency and wheel smoke
```

不创建新 branch、不创建 PR，直接在唯一 `main` 上提交并推送。

### 10.2 推送后验收

推送后必须等待完整 `Kairo CI (kernel + TUI)` 结束。

最终必须是：

- Ubuntu Python 3.11、3.12、3.13、3.14：全部成功。
- Windows Python 3.11、3.12、3.13、3.14：全部成功。
- macOS Python 3.11、3.12、3.13、3.14：全部成功。
- Alpha wheels：成功。
- 总计 13/13 job 成功。

如果 GitHub CLI 报认证失效，先由用户执行：

```powershell
gh auth login -h github.com
gh auth status
```

不得在未登录时声称已经读取失败日志。公开 REST API 可用于确认 job 状态，但下载完整 job 日志需要有效权限。

## 11. 最终交付物

执行者必须提供：

1. 新提交 SHA。
2. 实际变更文件列表。
3. `GatedProvider` 如何绑定 script、如何回收 pending task。
4. session-chip 测试如何等待真实挂载状态。
5. Alpha job 新增的依赖安装步骤。
6. 50 轮并发测试结果。
7. Kernel/TUI 全量测试、Ruff、Mypy 结果。
8. 两个 wheel 未变化的 SHA256 证明。
9. 新 GitHub Actions 链接。
10. 13/13 job 成功列表。
11. 本地与远端最终 SHA。
12. 本地和远端均只有 `main` 的证明。

## 12. 完成定义

以下条件必须同时成立：

- 本地不再复现 session chip `NoMatches`。
- 50 轮 session 并发测试全部通过。
- `GatedProvider` 不遗留 pending task，A/B script 不会因调度顺序互换。
- Alpha wheel job 在干净 runner 上通过。
- 本地 Kernel、TUI、Ruff、Mypy、wheel 检查全绿。
- `git diff --check` 零输出。
- 现有两个 wheel SHA256 未变化。
- GitHub Actions 13/13 job 全绿。
- 本地与远端只有一个 `main`，并指向同一最新 SHA。

缺少任意一项，本轮仍判定为未完成，不得以本地偶尔通过代替跨平台验收。
