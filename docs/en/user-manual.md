# Kairo Complete User Manual

Version: **0.2.7-beta**

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

Prefer `api_key_env` so secrets are not written back to disk. You can also use inline `api_key` for local-only use; manage keys safely from `/settings` > Keys.

Legacy `llm.providers[]` configs continue to work and are converted to profiles automatically.

## 3. Interface

Kairo has two renderers:

- **Textual TUI**: default. Includes the conversation view, composer, slash command palette, Kai status animation, Workspace Dock, and context progress.
- **Plain mode**: start with `--plain`; useful for unsupported terminals, CI, log redirection, or debugging.

On wide terminals, the Dock shows the workspace tree, touched files, Git/non-Git diffs, model, session, context, tokens, modes, and task state. On narrow terminals, this collapses to a bottom bar; use `/workspace` or `Ctrl+B` to open the workspace view.

## 4. Slash Commands

Type `/` to open the command palette. Keep typing to filter by prefix. Use `Up/Down` to select, `Tab` or `Enter` to complete, and `Esc` to close.

0.2.7-beta reduced the default command surface from 52 commands to 18 workflow-oriented entries. Fine-grained provider/model/key/session/config commands were removed and moved into interactive panels.

| Command | Purpose | Example |
| --- | --- | --- |
| `/help` | Show grouped help | `/help` |
| `/exit` | Exit Kairo | `/exit` |
| `/new [name]` | Create and switch to a new persisted session | `/new Refactor auth` |
| `/sessions` | Open the session management panel | `/sessions` |
| `/clear` | Clear the current session without deleting its file | `/clear` |
| `/undo` | Undo the latest user turn and following response | `/undo` |
| `/compress` | Manually compress older context | `/compress` |
| `/model` | Switch the current chat profile | `/model` |
| `/setup` | Run the first-run setup wizard | `/setup` |
| `/settings` | Open the settings/config panel | `/settings` |
| `/mode` | Open the mode panel (authorization, plan, thinking) | `/mode` |
| `/workspace [path-or-bookmark]` | Open workspace panel or hot-switch workspace | `/workspace C:\repo\app` |
| `/status` | Show read-only runtime status | `/status` |
| `/find <keyword>` | Search current and persisted sessions | `/find auth` |
| `/export` | Export session or config | `/export` |
| `/doctor` | Run health checks | `/doctor` |
| `/skills` | List loaded built-in tools and custom skills | `/skills` |
| `/docs` | Show local documentation index | `/docs` |

### Removed commands and migration (0.2.7-beta)

| Removed command | Use instead |
| --- | --- |
| `/manual` `/auto` `/yolo` `/plan` `/think` | `/mode` |
| `/providers` `/provider add|edit|remove|test` | `/settings` > Providers |
| `/model add|edit|remove|test` | `/settings` > Models |
| `/keys` `/key set|clear|reveal|migrate` | `/settings` > Keys |
| `/roles` `/role set|clear` | `/settings` > Roles |
| `/config validate|backup|restore|export|import` | `/settings` > Config or `/export` |
| `/session rename|delete|export|reveal|search|open` | `/sessions` |
| `/workspace save` `/workspaces` `/workspace remove` | `/workspace` |
| `/docs config` `/docs providers` `/docs sessions` | `/docs` |

`/model` is now switch-only; all provider/model/key/role/config management is in `/settings`. `/workspace move <path>` is now `/workspace <path-or-bookmark>`.

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
| `Esc` | Close menus or modals; while busy (streaming/running tools), stop the current generation (0.2.6) |

The composer grows with physical and soft-wrapped lines up to 8 visible lines.

## 6. Model Profiles

Kairo reads selectable model profiles from `config.json` under `llm.profiles[]` (0.2.5) or legacy `llm.providers[].models[]`. Use `/model` to choose one. Since 0.2.6 `/model` is a single transaction that switches the **chat profile**: it keeps `llm.active_profile` and `model_roles.chat` consistent and syncs the context window, runtime state and all sessions, so the next chat request is guaranteed to use the selected profile. After switching:

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

- `chat` — default user-facing chat. Updated by `/model` when configured.
- `plan` — plan/thinking mode.
- `compress` — context compression summaries.
- `fast` — quick internal tasks.

Use `/settings` > Roles to bind or unbind roles. If a role is unbound, the active profile is used. `/model` only affects the `chat` route; `plan`/`compress`/`fast` are not changed.

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
/workspace C:\Users\Admin\Desktop\project\my-app
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
/workspace
/workspace C:\Users\Admin\Desktop\project\app
/workspace app
```

The `/workspace` panel lets you save and remove bookmarks. Bookmarks are stored in `config.json` under `workspace_bookmarks` and persist across restarts. Use `/workspace <bookmark-name>` to switch quickly.

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
- `/session search` and `/session open` read-only session lookup.
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

Run `/workspace` to confirm the active root. A short empty state can appear while the background scan refreshes. If the target path does not exist or is not writable, `/workspace <path>` reports an error.

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
/mode
```

and select Manual authorization, or start with:

```powershell
kairo --authorization manual
```

## 8. Runtime Configuration and Panels (0.2.7-beta)

Kairo can create, edit, and remove model profiles while the TUI is running, without restarting or editing `config.json`. Legacy `llm.providers[]` configs are converted to profiles automatically. Since 0.2.7-beta, all fine-grained provider/model/key/role/config commands were moved into interactive panels; the slash command surface was reduced to workflow-oriented entries.

### 8.1 `/settings` — Config Panel

`/settings` opens the central configuration panel. It covers everything that used to be split across `/providers`, `/model`, `/keys`, `/roles`, and `/config` commands:

- **Providers**: list, add, edit, remove, and test providers.
- **Models**: list, add, edit, remove, and test models.
- **Keys**: list key sources, set inline keys, clear keys, reveal, and migrate legacy keys.
- **Roles**: bind or unbind `chat`/`plan`/`compress`/`fast` roles to profiles.
- **Config**: validate, backup, restore, import, and export the current configuration.

Provider/model tests send a minimal OpenAI-compatible probe; the probe is not written to session history and does not trigger context compression.

### 8.2 `/setup` — First-Run Wizard

`/setup` runs the first-run setup wizard for new installs or invalid active profiles. It walks through creating a profile/provider, setting the base URL, model, API key mode (inline or env), and parameters, then runs a minimal connection test and saves the config with an automatic backup.

### 8.3 `/mode` — Authorization and Modes

`/mode` replaces `/manual`, `/auto`, `/yolo`, `/plan`, and `/think`. It opens a compact panel for:

- **Authorization**: Manual / Auto / YOLO.
- **Plan Mode**: ON / OFF.
- **Thinking Mode**: ON / OFF.

### 8.4 `/status` — Runtime Status

`/status` shows a read-only runtime summary:

- Kairo version.
- Current chat profile, model, and base URL.
- API key source and masked state.
- Current session name, id, and message count.
- Context used / context window / percent.
- Workspace root.
- Plan / Thinking / Authorization state.
- Session persistence and strict message packing status.

No full API keys or unredacted config JSON are shown.

### 8.5 `/sessions` — Session Management

`/sessions` opens the session management panel. It covers everything that used to be split across `/session` subcommands:

- Switch active session.
- Search sessions by title or content (`/find <keyword>` is a shortcut).
- Open a session from search results.
- Rename or delete a session (the last active session cannot be deleted).
- Export the current session as Markdown or JSON.
- Reveal the on-disk path of the current session.

### 8.6 `/workspace [path-or-bookmark]` — Workspace Panel and Hot Switch

`/workspace` with no argument opens the workspace panel, showing the current root, bookmarks, file tree, changed files, and diff. With an argument, it hot-switches to the given path or bookmark name in the current process: tool roots, shell cwd, Python REPL, custom skills, Dock tree, and session runtime state all update without restarting Kairo.

`/workspace move <path>` from earlier releases is now `/workspace <path-or-bookmark>`.

### 8.7 `/export` — Unified Export

`/export` opens the export panel:

- Current session as Markdown.
- Current session as JSON.
- Config export, redacted by default.
- Config export with keys, requiring explicit confirmation.

### 8.8 API Key Safety

- **Local deployment default**: inline `api_key` values are allowed in `config.json` for local, single-user use. Keep `config.json` out of version control and never commit it.
- **Recommended for shared/CI projects**: store keys in environment variables and reference them with `api_key_env`. Env keys are never written back to `config.json`.
- Key reveal and config export with keys require explicit confirmation.
- `/status`, logs, session history, and `/doctor` show only masked previews; full keys are never printed.

### 8.9 How Runtime Configuration Works

Runtime configuration does not ask you to edit JSON by hand. Kairo uses a safer command -> panel -> draft -> validate -> backup -> save -> hot-switch flow.

1. **Command entry**: type `/settings` or `/setup`.
2. **Input collection**: in the Textual TUI, Kairo opens a modal form; in plain mode, Kairo asks the same questions step by step.
3. **ConfigDraft first**: your answers are written into an in-memory `ConfigDraft`, not directly into `config.json`.
4. **Validation**: before saving, Kairo checks duplicate provider/profile names, URL shape, active profile validity, `context_window`, `max_tokens`, and `temperature`.
5. **API key handling**: with `env` mode, `config.json` stores only the `api_key_env` variable name; with `inline` mode, Kairo asks for explicit confirmation because the key will be written to disk.
6. **Automatic backup**: before saving, Kairo writes `config.backup.YYYYMMDD-HHMMSS.json`.
7. **Atomic save and rollback**: Kairo writes through a temporary file and replaces the config. If saving fails, the previous config remains available.
8. **Immediate activation**: after saving, Kairo reloads the active profile and updates `base_url`, `model`, `temperature`, `max_tokens`, `context_window`, and context-management settings.
9. **Session integration**: the active session runtime state records the new model profile; the Dock refreshes the model name and context limit.

The result is equivalent to editing `config.json` manually, but with validation, backup, API-key safety, and live updates for the current session.

### 8.10 `/doctor`

`/doctor` runs a health dashboard that checks config validity, key presence, workspace reachability, session storage, git state, and provider reachability. No secrets are printed.

## 9. What’s New in 0.2.7-beta

- **Slash command redesign**: the default command surface was reduced from 52 commands to 18 workflow-oriented entries.
- **Panel-based management**: provider/model/key/role/config management moved to `/settings`; session management moved to `/sessions`; workspace management moved to `/workspace`.
- **New commands**: `/setup` (first-run wizard), `/mode` (authorization/plan/thinking), `/status` (read-only runtime status), `/find` (session search), and `/export` (unified export).
- **Removed subcommands**: `/manual`, `/auto`, `/yolo`, `/plan`, `/think`, `/provider ...`, `/model add|edit|remove|test`, `/key ...`, `/role ...`, `/config ...`, `/session ...`, `/workspace save|remove`, and `/docs config|providers|sessions`. Removed commands now show migration hints.
- **`/workspace <path-or-bookmark>`**: the old `/workspace move <path>` is now the argument form of `/workspace`.
- **`/model` is switch-only**: it selects the chat profile; editing is in `/settings`.

## 10. What’s New in 0.2.6-beta

- **Unified `/model` switch**: `/model` now switches the chat profile through a single transaction that keeps `model_roles.chat`, `active_profile`, context window and sessions consistent — the next chat request actually uses the selected profile.
- **Provider key preservation**: editing one provider no longer clears other providers' inline API keys. Blank key input keeps the existing key; explicit clear only clears the target.
- **Strict message packing**: all LLM request payloads are folded into a single leading `system` message for strict OpenAI-compatible providers (`llm.strict_message_packing`, default `true`).
- **Esc stop generation**: in the Textual UI, press `Esc` while streaming or running tools to cooperatively stop the current output; the partial reply is saved with a `[stopped]` marker. Plain mode still uses `Ctrl+C`.

## 11. What’s New in 0.2.5

- Profile-first config: `llm.profiles[]` with legacy `llm.providers[]` automatic migration.
- Local config-first key management with mask-by-default safety.
- Model roles: `chat`, `plan`, `compress`, `fast` routing via `llm.model_roles`.
- Workspace bookmarks and hot switching.
- Session search and switch.
- Config import/export with redaction by default and with-keys confirmation.
- `/doctor` health dashboard.
- Updated configuration docs, user manuals, and expanded tests.
