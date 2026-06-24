# Kairo Complete User Manual

Version: **0.2.5**

Kairo is a terminal-native AI coding agent. It uses a Textual full-screen TUI by default and also supports a `--plain` compatibility mode. It connects to OpenAI-compatible models and can work with local files, search, patching, shell commands, Python execution, web fetching, context compression, persisted conversations, custom skills, and runtime provider/model configuration.

## 1. Install And Start

### Windows

```powershell
.\run.bat
```

Manual setup:

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

### Common CLI Flags

| Flag | Purpose |
| --- | --- |
| `--config <path>` | Use a specific config file |
| `--plain` | Use compatible non-full-screen output |
| `--tui` | Force the Textual TUI |
| `--no-animation` | Disable animations |
| `--reduced-motion` | Use reduced-motion UI states |
| `--authorization manual|auto|yolo` | Set the tool authorization level |
| `--auto` | Shortcut for `auto` authorization |
| `--plan` | Start with Plan Mode enabled |
| `--think` | Start with Thinking Mode enabled |

## 2. First Configuration

Copy the template:

```powershell
Copy-Item config.example.json config.json
```

Store API keys in environment variables (recommended for shared projects):

```powershell
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
```

Profile example:

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

Prefer `api_key_env` so secrets are not written back to disk. You can also use inline `api_key` for local-only use; run `/key set` to manage keys safely.

Legacy `llm.providers[]` configs continue to work and are converted to profiles automatically.

## 3. Interface

Kairo has two renderers:

- **Textual TUI**: default. Includes the conversation view, composer, slash command palette, Kai status animation, Workspace Dock, and context progress.
- **Plain mode**: start with `--plain`; useful for unsupported terminals, CI, log redirection, or debugging.

On wide terminals, the Dock shows the workspace tree, touched files, Git/non-Git diffs, model, session, context, tokens, modes, and task state. On narrow terminals, this collapses to a bottom bar; use `/workspace` or `Ctrl+B` to open the workspace view.

## 4. Slash Commands

Type `/` to open the command palette. Keep typing to filter by prefix. Use `Up/Down` to select, `Tab` or `Enter` to complete, and `Esc` to close.

| Command | Purpose | Example |
| --- | --- | --- |
| `/help` | Show help | `/help` |
| `/exit` | Exit Kairo | `/exit` |
| `/config` | Show current settings | `/config` |
| `/config export` | Export config with keys redacted | `/config export --with-keys` |
| `/config import` | Import a config file | `/config import backup.json` |
| `/model` | Select a configured model profile | `/model` |
| `/keys` | List configured keys | `/keys` |
| `/key set` | Set a profile key | `/key set deepseek-chat` |
| `/key clear` | Remove a profile key | `/key clear deepseek-chat` |
| `/key reveal` | Show the active profile key | `/key reveal` |
| `/roles` | List model roles | `/roles` |
| `/role set` | Bind a role to a profile | `/role set plan deepseek-reasoner` |
| `/role clear` | Unbind a role | `/role clear fast` |
| `/manual` | Confirm every tool call | `/manual` |
| `/auto` | Auto-run normal in-workspace tools; still confirm external/system/destructive actions | `/auto` |
| `/yolo` | Skip tool confirmations; use carefully | `/yolo` |
| `/plan` | Toggle Plan Mode | `/plan` |
| `/think` | Toggle Thinking Mode | `/think` |
| `/skills` | List loaded built-in tools and custom skills | `/skills` |
| `/new [name]` | Create and switch to a new persisted session | `/new Refactor auth` |
| `/sessions` | Switch saved sessions | `/sessions` |
| `/session search` | Search saved sessions read-only | `/session search auth` |
| `/session open` | Switch to a saved session found by search index or id | `/session open 3` |
| `/clear` | Clear the current session without deleting its file | `/clear` |
| `/undo` | Undo the latest user turn and following response | `/undo` |
| `/compress` | Manually compress older context | `/compress` |
| `/workspace` | Show the current workspace | `/workspace` |
| `/workspace move <path\|name>` | Hot-switch workspace without restarting | `/workspace move C:\repo\app` |
| `/workspace save` | Bookmark the current workspace | `/workspace save app` |
| `/workspaces` | List workspace bookmarks | `/workspaces` |
| `/workspace remove` | Remove a workspace bookmark | `/workspace remove app` |
| `/doctor` | Run health checks | `/doctor` |

## 5. Keyboard And Composer

| Key | Purpose |
| --- | --- |
| `Enter` | Submit |
| `Shift+Enter` / `Ctrl+Enter` | Insert a newline |
| `Ctrl+Up` / `Ctrl+Down` | Browse input history |
| `Ctrl+B` | Focus Workspace Dock on wide terminals; open Workspace Modal on narrow terminals |
| `Ctrl+A` | Cycle `manual -> auto -> yolo` |
| `Ctrl+P` | Toggle Plan Mode |
| `Ctrl+T` | Toggle Thinking Mode |
| `Esc` | Close menus or modals |

The composer grows with physical and soft-wrapped lines up to 8 visible lines.

## 6. Model Profiles

Kairo reads selectable model profiles from `config.json` under `llm.profiles[]` (0.2.5) or legacy `llm.providers[].models[]`. Use `/model` to choose one. After switching:

- model, base URL, temperature, max tokens, and context window update immediately;
- the Dock recalculates the context limit;
- session runtime state records the active profile.

### 6.1 Model Roles (0.2.5)

`llm.model_roles` lets you route different tasks to different profiles:

```json
"model_roles": {
  "chat": "deepseek-chat",
  "plan": "deepseek-reasoner",
  "compress": "deepseek-chat",
  "fast": "local-llm"
}
```

Roles:

- `chat` — default user-facing chat.
- `plan` — plan/thinking mode.
- `compress` — context compression summaries.
- `fast` — quick internal tasks.

Use `/role set <role> <profile>` to bind a role and `/role clear <role>` to unbind. If a role is unbound, the active profile is used.

## 7. Persisted Sessions

Since 0.2.2, sessions are persisted by default:

```json
"sessions": {
  "enabled": true,
  "storage_dir": ".kairo/sessions",
  "autosave": true,
  "save_interval_seconds": 1.0,
  "max_sessions": 200
}
```

Behavior:

- each session is stored as a separate JSON file;
- `index.json` tracks session metadata and the last active session;
- `/new` creates a new session file;
- `/sessions` switches between saved sessions;
- responses, tool results, compression, undo, clear, model switches, workspace moves, and shutdown trigger saves;
- set `sessions.enabled=false` to use in-memory sessions only.

Session files may contain prompts, code, file contents, command output, and secrets. The default `.kairo/` path is ignored by Git.

## 8. Context Management

The Dock shows:

```text
Context: ≈used / limit (percent)
```

Concepts:

- `session_input_tokens/session_output_tokens`: cumulative session usage.
- `context_used_tokens`: estimated request payload size.
- `/compress`: summarize older history while keeping the system prompt, runtime state, and recent turns.
- automatic compression: triggered near the configured limit or when output budget needs more room.
- trimming: if compression fails or still exceeds budget, Kairo removes oldest complete turns.

Progress colors:

- below 60%: normal;
- 60% to trigger threshold: warning;
- at/above threshold: high risk and likely compression.

## 9. Workspace And Dock

The workspace is the project root Kairo treats as the normal operating boundary:

```json
"workspace_root": "."
```

Common commands:

```text
/workspace
/workspace move C:\Users\Admin\Desktop\project\my-app
```

0.2.2 hot-switch behavior:

- file, patch, and search tools move to the new root;
- the persistent shell restarts in the new workspace;
- the Python REPL resets to avoid leaking old variables or path state;
- custom skills reload from the new workspace;
- active conversation runtime state records the new root before the next model call;
- Dock tree and diff refresh.

### 9.1 Workspace Bookmarks (0.2.5)

Save frequently used workspaces:

```text
/workspace save app
/workspaces
/workspace move app
/workspace remove app
```

Bookmarks are stored in `config.json` under `workspace_bookmarks` and persist across restarts. Use `/workspace move <bookmark-name>` to switch quickly.

Workspace Review is read-only. It does not stage, restore, delete, or rewrite files by itself.

## 10. Tools And Authorization

Built-in tools:

| Tool | Capability |
| --- | --- |
| `read_file` | Read files inside the workspace |
| `write_file` | Write or overwrite files |
| `list_dir` | List directories |
| `search_file` | Search text or regex |
| `patch_file` | Apply exact search/replace patches |
| `run_command` | Run commands in a persistent shell |
| `run_python_code` | Run code in a restricted Python REPL |
| `web_fetch` | Fetch web page content |
| custom skills | Load user-defined tools from `skills_dir` |

Authorization levels:

- `manual`: ask before every tool call.
- `auto`: auto-run normal in-workspace operations; ask for external, system, or destructive operations.
- `yolo`: skip confirmations; use only when you accept the risk.

## 11. Custom Skills

Default setting:

```json
"skills_dir": "./skills"
```

Relative paths resolve against the active workspace. After a workspace move, Kairo unloads old custom skills and reloads skills from the new workspace.

Minimal skill:

```python
from tools.base import skill

@skill(name="hello_skill", description="Return a greeting")
def hello_skill(name: str = "Kairo"):
    return f"Hello, {name}"
```

Save it as `skills/hello_skill.py`, then restart Kairo or move workspaces to trigger a reload.

## 12. Version History

### 0.2.5

Added:

- `llm.profiles[]`, `active_profile`, `model_roles`, and `ProfileResolver`.
- `/keys`, `/key set|clear|reveal|migrate` commands.
- `/roles`, `/role set|clear` commands.
- `workspace_bookmarks` and `/workspace save|remove`, `/workspaces` commands.
- `/session search` read-only lookup and `/session open` switching by search index or session id.
- `/config export` and `/config import` with key redaction.
- `/doctor` health dashboard.
- Expanded test coverage for 0.2.5 features.

Changed:

- Inline API keys are the local-deployment default; env keys remain supported for shared/CI use.
- `/config`, logs, session history, and `/doctor` mask API keys by default.
- `config.example.json` uses the new `llm.profiles[]` format.

### 0.2.2

Added:

- persisted sessions with separate JSON files, `index.json`, and active-session restore;
- `sessions` config: `enabled`, `storage_dir`, `autosave`, `save_interval_seconds`, `max_sessions`;
- hot workspace switching for runtime state, tool roots, shell cwd, Python REPL reset, and skill reload;
- session store and workspace hot-switch tests.

Changed:

- `/new` and `/sessions` now work with persisted sessions;
- `/workspace move <path>` is an immediate current-process transition;
- user docs now describe persistence and hot switching.

Fixed:

- search results use the active workspace as the relative-path base;
- model requests receive current workspace state instead of relying on stale conversation text.

### 0.2.1

Added:

- authorization levels: `manual`, `auto`, `yolo`;
- `/workspace move <path>`;
- scrollable Slash Command palette;
- Workspace Dock with file tree, touched files, and Git/non-Git diffs;
- responsive Dock width, context progress bar, multiline composer, and wide-content rendering fixes.

Fixed:

- `/config` no longer crashes the Textual UI;
- stale workspace scans no longer overwrite the latest Dock state;
- switching between directories with identical file structures refreshes the tree;
- Windows modifier handling for `Shift+Enter` and `Ctrl+Enter`.

### 0.2.0

Added:

- Kairo branding and Kai terminal mascot;
- Textual full-screen TUI, animation, Dock, and plain fallback;
- model profiles, context management, and in-process multi-session support;
- `/compress`, `/new`, and `/sessions`.

### 0.1.0

Added:

- pyTUI prototype;
- Rich CLI, OpenAI-compatible streaming client, and basic local tools.

## 13. Troubleshooting

### `/model` shows no profiles

Check `llm.profiles[]` or legacy `llm.providers[]`, profile IDs, `active_profile`, and role bindings in `llm.model_roles`.

### API key is not picked up

Check the environment variable:

```powershell
echo $env:KAIRO_DEEPSEEK_API_KEY
```

### Sessions are not saved

Check:

- `sessions.enabled` is `true`;
- `sessions.storage_dir` is writable;
- Kairo is not running from a read-only directory.

### Dock does not update after workspace move

Run `/workspace` to confirm the active root. A short empty state can appear while the background scan refreshes. If the target path does not exist or is not writable, `/workspace move` reports an error.

### TUI does not work in the current terminal

Use:

```powershell
kairo --plain
```

or:

```powershell
kairo --reduced-motion
```

### Disable automatic tools

Use:

```text
/manual
```

or:

```powershell
kairo --authorization manual
```

## 8. Runtime Configuration (0.2.3 + 0.2.5)

Kairo can create, edit, and remove model profiles while the TUI is running, without restarting or editing `config.json`. Legacy `llm.providers[]` configs are converted to profiles automatically.

### 8.1 Provider Management

- `/providers` — list configured providers.
- `/provider add` — start the add-provider wizard.
- `/provider edit` — edit an existing provider.
- `/provider remove` — remove a provider (at least one must remain).
- `/provider test` — send a minimal probe; results: Success / Auth Error / Model Error / URL Error / Rate Limit / Unknown.

### 8.2 Model Management

- `/model add` — add a new model to a provider.
- `/model edit` — edit context window, max tokens, and temperature.
- `/model remove` — remove a model (at least one per provider must remain).
- `/model test` — test a specific model on its provider.

### 8.3 Config Backup and Restore

- `/config validate` — validate the current configuration.
- `/config backup` — write a timestamped `config.backup.YYYYMMDD-HHMMSS.json`.
- `/config restore` — pick a backup and restore it.

### 8.4 API Key Safety

- **Local deployment default** (0.2.5): inline `api_key` values are allowed in `config.json` for local, single-user use. Keep `config.json` out of version control and never commit it.
- **Recommended for shared/CI projects**: store keys in environment variables and reference them with `api_key_env`. Env keys are never written back to `config.json`.
- `/key reveal` and `/config export --with-keys` require explicit confirmation.
- `/config`, logs, session history, and `/doctor` show only masked previews; full keys are never printed.

### 8.5 Key and Role Management (0.2.5)

- `/keys` — list profiles and their key source (env / inline / missing).
- `/key set <profile-id> [value]` — set an inline key; prompts securely if no value is given.
- `/key clear <profile-id>` — remove the inline key from the profile.
- `/key reveal` — show the active profile's key after confirmation.
- `/key migrate` — migrate inline API keys from legacy `llm.providers[]` into the matching profile keys in `llm.profiles[]` (one-time upgrade from 0.2.4 configs).
- `/roles` — show current role bindings.
- `/role set <role> <profile-id>` — bind chat/plan/compress/fast to a profile.
- `/role clear <role>` — remove a binding.

### 8.6 Config Import and Export (0.2.5)

- `/config export [<path>]` — export a clean copy of the current config with all `api_key` fields redacted by default.
- `/config export --with-keys [<path>]` — export with plaintext keys; requires confirmation.
- `/config import <path>` — import a config file after validation; creates a backup of the current config first.

### 8.7 Doctor (0.2.5)

- `/doctor` — run a health dashboard that checks config validity, key presence, workspace reachability, session storage, git state, and provider reachability. No secrets are printed.

### 8.8 First-Run Wizard

When `config.json` is missing, `llm.profiles` is empty, or the active profile is invalid:

- Plain mode runs an interactive first-run wizard after startup.
- TUI mode shows a notice directing you to `/provider add`; you can skip it and configure later.

### 8.9 How Terminal Model Configuration Works

Runtime model configuration does not ask you to edit JSON by hand. Kairo uses a safer command -> draft -> validate -> backup -> save -> hot-switch flow.

1. **Command entry**: type `/provider add`, `/provider edit`, `/model add`, `/model edit`, or `/settings`.
2. **Input collection**: in the Textual TUI, Kairo opens a modal form; in plain mode, Kairo asks the same questions step by step.
3. **ConfigDraft first**: your answers are written into an in-memory `ConfigDraft`, not directly into `config.json`.
4. **Validation**: before saving, Kairo checks duplicate provider names, URL shape, active provider/model validity, `context_window`, `max_tokens`, and `temperature`.
5. **API key handling**: with `env` mode, `config.json` stores only the `api_key_env` variable name, never the actual environment variable value; with `inline` mode, Kairo asks for explicit confirmation because the key will be written to `config.json`; `/config` shows only the key source and a safe preview.
6. **Automatic backup**: before saving, Kairo writes `config.backup.YYYYMMDD-HHMMSS.json`.
7. **Atomic save and rollback**: Kairo writes through a temporary file and replaces the config. If saving fails, the previous config remains available.
8. **Immediate activation**: after saving, Kairo reloads the active provider/model and updates `base_url`, `model`, `temperature`, `max_tokens`, `context_window`, and context-management settings.
9. **Session integration**: the active session runtime state records the new model profile; the Dock refreshes the model name and context limit.
10. **Connection testing**: `/provider test` and `/model test` send a minimal OpenAI-compatible probe. The probe is not written to session history and does not trigger context compression.

The result is equivalent to editing `config.json` manually, but with validation, backup, API-key safety, and live updates for the current session.

### 8.7 Session Organization

- `/session rename` — rename the current session.
- `/session delete` — delete a session with confirmation (you cannot delete the last active session).
- `/session export` — export the current session as Markdown or JSON to `<storage_dir>/exports/`.
- `/session reveal` — print the absolute path of the current session file.
- `/session search <keyword>` — search saved sessions by title or content (read-only).
- `/session open <id-or-index>` — switch to a saved session found by search index or session id.

## 9. What’s New in 0.2.5

- Profile-first config: `llm.profiles[]` with legacy `llm.providers[]` automatic migration.
- Local config-first key management: `/keys`, `/key set|clear|reveal|migrate` with mask-by-default safety.
- Model roles: `chat`, `plan`, `compress`, `fast` routing via `/role set` and `llm.model_roles`.
- Workspace bookmarks: `/workspace save`, `/workspaces`, `/workspace move <name>`, `/workspace remove`.
- Session search and switch: `/session search`, `/session open`.
- Config import/export with redaction by default and `--with-keys` confirmation.
- `/doctor` health dashboard.
- Updated configuration docs, user manuals, and expanded tests.
