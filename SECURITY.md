# Security

## 威胁模型

Kairo 是本地执行型 Agent，不是隔离沙箱。Shell、Python、文件写入和动态 skill 都能以当前用户权限运行。模型输出也可能受到提示注入影响。

## 安全使用

- 默认保持 `manual` 授权级别，逐项审查工具参数。
- 在专用工作目录运行，不要从含敏感文件的主目录启动。
- API Key 只通过环境变量或 secret manager 提供。
- 不加载来源不明的 `skills/` Python 文件。
- 在运行真实 provider harness 前确认费用、目录和模型 profile。

## 工具权限与工作区边界

自 0.2.x 起，内置工具受 `policy` 配置约束：

- `workspace_path.allow_absolute_outside`（默认 `false`）：`read_file`、`write_file`、`list_dir`、`search_file`、`patch_file` 的目标路径必须位于启动工作区内，禁止 `..` 逃逸。
- `network.allow_hosts/deny_hosts` 与 `deny_private_loopback`（默认 `true`）：`web_fetch` 默认不能访问私有/回环地址。
- `command.allow_patterns/deny_patterns` 与 `require_confirmation_for_chained`（默认 `true`）：`run_command` 对含 `;`、`&&`、`|` 等 shell 链式字符的命令要求二次确认。
- `python.deny_builtins/deny_modules`：`run_python_code` 禁用 `exec`、`open`、`os`、`subprocess` 等危险内建和模块。
- `skills.require_hash`（默认 `false`）：开启后，`skills/` 目录下的 `.py` 必须附带同名的 `.py.sha256` 摘要文件才能加载。

这些策略是**最小可行沙箱**，不能替代操作系统级隔离。处理不可信代码或高敏感数据时，应在虚拟机/容器中使用 Kairo。

## 凭据处理

`config.json` 已被 Git 忽略，仓库只提供 `config.example.json`。`llm.providers[]` 使用 `api_key_env` 指向环境变量；环境注入的值不会由 `Config.save()` 写回磁盘。配置文件中显式写入的 `api_key` 仍会保留，因此建议优先使用 `api_key_env`。

如果密钥曾进入工作树、日志、截图、终端历史或 Git 历史，应视为已泄露并在 provider 后台轮换。仅从最新文件中删除并不足够。

## 报告问题

当前仓库尚未配置公开安全联系人。建立正式托管仓库时，应在此补充私密报告渠道、响应时限和受支持版本，避免公开 issue 披露有效凭据或可利用细节。
