# 配置指南

默认读取工作目录下的 `config.json`，也可用 `--config PATH` 指定其它路径。

## 加载优先级

AI 相关配置优先级从高到低：

1. provider 的 `api_key_env` 对应的环境变量；
2. 通用环境变量覆盖：`OPENAI_API_KEY` / `GEMINI_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL`、`CONTEXT_WINDOW`；
3. `llm.providers[]` 中活动 provider 的本地字段；
4. `llm.defaults`；
5. 代码默认值。

`SHELL_TYPE` 仍可通过环境变量覆盖。

## 推荐配置结构

```json
{
  "llm": {
    "active_provider": "deepseek",
    "active_model": "deepseek-chat",
    "defaults": {
      "temperature": 0.2,
      "max_tokens": 4000,
      "context_window": 128000
    },
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
  },
  "workspace_root": ".",
  "authorization_level": "manual",
  "plan_mode": false,
  "thinking_mode": false,
  "context_management": {
    "enabled": true,
    "auto_compress": true,
    "trigger_percent": 85,
    "target_percent": 60,
    "preserve_recent_turns": 4
  },
  "sessions": {
    "enabled": true,
    "storage_dir": ".kairo/sessions",
    "autosave": true,
    "save_interval_seconds": 1.0,
    "max_sessions": 200
  },
  "ui": {
    "mode": "auto",
    "theme": "kairo-dark",
    "animation": "full",
    "mascot": true,
    "dock_breakpoint": 120,
    "dock_width_ratio": 0.333,
    "dock_min_width": 36,
    "dock_max_width": 64,
    "reduced_motion": false,
    "workspace_enabled": true,
    "workspace_refresh_seconds": 2.0,
    "workspace_max_files": 2000,
    "workspace_diff_max_bytes": 204800
  },
  "policy": {
    "workspace_path": { "allow_absolute_outside": false },
    "network": {
      "allow_hosts": [],
      "deny_hosts": [],
      "deny_private_loopback": true
    },
    "command": {
      "allow_patterns": [],
      "deny_patterns": [],
      "require_confirmation_for_chained": true
    },
    "python": {
      "deny_builtins": ["exec", "eval", "compile", "__import__", "open"],
      "deny_modules": ["os", "subprocess", "sys", "socket", "urllib"]
    },
    "skills": { "require_hash": false },
    "resource_limits": {
      "max_read_bytes": 1048576,
      "max_search_bytes": 1048576,
      "max_fetch_bytes": 1048576,
      "max_search_depth": 10,
      "max_search_results": 100
    }
  },
  "skills_dir": "./skills",
  "shell_type": "cmd"
}
```

## 字段说明

### `llm`

- `active_provider`：当前活动供应商。
- `active_model`：当前活动模型。
- `defaults`：公共生成默认值。
- `providers[]`：OpenAI-compatible 供应商列表。
  - `base_url`：API 根路径。
  - `api_key`：允许本地明文，仅限本地私有配置。
  - `api_key_env`：推荐方式；环境变量存在时优先于本地明文。
  - `models[]`：该供应商下可选模型及预算配置。

### 运行配置

- `workspace_root`：workspace 根目录，默认 `.`。
- `authorization_level`：`manual`、`auto` 或 `yolo`。
- `plan_mode`：是否默认开启 Plan Mode。
- `thinking_mode`：是否默认开启 Thinking Mode。

### `context_management`

- `enabled`：是否启用上下文管理。
- `auto_compress`：达到阈值时是否自动压缩。
- `trigger_percent`：触发压缩/裁剪的占用率。
- `target_percent`：压缩或裁剪后的目标占用率。
- `preserve_recent_turns`：优先保留的最近完整轮次数。

### `sessions`

- `enabled`：是否启用 session 持久化。`false` 时退回纯内存行为，方便测试和临时会话。
- `storage_dir`：session 存储目录。支持绝对路径；相对路径相对于 `config.json` 所在目录解析。
- `autosave`：是否在 history 变化、压缩、撤销、清空、切换模型、切换 workspace 等关键操作后自动保存。
- `save_interval_seconds`：自动保存间隔（0.2.2 中作为保留字段，当前实现为关键路径直接保存）。
- `max_sessions`：启动时自动加载的最大 session 数量，不自动删除用户文件。

Session 文件中可能包含代码、命令输出、文件内容或敏感信息，建议将 `storage_dir` 加入 `.gitignore`。默认 `.kairo/sessions` 已被忽略。

### `ui`

- `mode`：`auto` 或 `plain`。
- `theme`：UI 主题名。
- `animation`：`none` 可完全关闭动画。
- `dock_breakpoint`：宽屏 Dock 与底部状态栏切换阈值。
- `dock_width_ratio`：宽屏 Dock 占终端宽度比例。
- `dock_min_width` / `dock_max_width`：Dock 宽度上下限。
- `workspace_refresh_seconds`：后台刷新间隔。
- `workspace_max_files`：文件树收集上限。
- `workspace_diff_max_bytes`：单文件 Diff 最大读取量。

### `policy`

- `workspace_path.allow_absolute_outside`：是否允许工具操作 workspace 外的绝对路径。
- `network.allow_hosts` / `deny_hosts`：网络请求白名单/黑名单。
- `network.deny_private_loopback`：是否拒绝私有/回环地址。
- `command`：Shell 命令 allow/deny 正则与链式字符确认。
- `python.deny_builtins` / `deny_modules`：Python REPL 禁用列表。
- `skills.require_hash`：是否要求 skill 文件附带 `.sha256` 摘要。
- `resource_limits`：文件读取、搜索、网页抓取的大小与深度限制。

`authorization_level` 只控制调用前是否弹出确认。即使使用 `yolo`，上述工具策略仍会执行并可能拒绝路径越界、受限网络、链式命令或受禁 Python 能力。

## LLM 传输行为

`LLMClient` 自动遵循进程环境中的 `HTTP_PROXY` 和 `HTTPS_PROXY`。当前重试策略是代码内默认行为，尚未暴露为 JSON 字段：最多重试 3 次，退避约为 1、2、4 秒；429、服务端错误和可重试的网络错误会重试，认证错误、普通客户端错误和上下文长度错误不会进入通用重试。上下文长度错误由 Agent 的上下文治理单独执行一次紧急压缩或裁剪重试。

## 启动参数

| 参数 | 说明 |
| --- | --- |
| `--config PATH` | 指定配置文件 |
| `--authorization {manual,auto,yolo}` | 设置启动授权级别 |
| `--auto` | 等价于 `--authorization auto` |
| `--plan` | 启动时开启 Plan Mode |
| `--think` | 启动时开启 Thinking Mode |
| `--plain` | 强制 plain 模式 |
| `--tui` | 强制 TUI 模式 |
| `--no-animation` | 禁用动画 |
| `--reduced-motion` | 低动态效果 |
| `--theme NAME` | 设置主题 |

## 旧配置兼容

旧版 `model_profiles` 结构仍可读取，启动时会归一化为新的 `llm.providers` 运行时结构；`save()` 会按新结构写回。

`auto_mode: true` 的旧字段会被映射为 `authorization_level: "auto"`，保存后不再写入 `auto_mode`。

旧 `ui.dock_width` 会迁移为比例 Dock 配置。新文件应使用 `dock_width_ratio`、`dock_min_width` 和 `dock_max_width`，不再写入 `dock_width`。
