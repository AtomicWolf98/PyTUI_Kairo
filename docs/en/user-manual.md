# Kairo Complete User Manual

Version: **0.2.2**

Kairo is a terminal-native AI coding agent. It uses a Textual full-screen TUI by default and also supports a `--plain` compatibility mode. It connects to OpenAI-compatible models and can work with local files, search, patching, shell commands, Python execution, web fetching, context compression, persisted conversations, and custom skills.

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

Store API keys in environment variables:

```powershell
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
```

Provider example:

```json
{
  "llm": {
    "active_provider": "deepseek",
    "active_model": "deepseek-chat",
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
  }
}
```

Prefer `api_key_env` so secrets are not written back to disk.

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
| `/model` | Select a configured model profile | `/model` |
| `/manual` | Confirm every tool call | `/manual` |
| `/auto` | Auto-run normal in-workspace tools; still confirm external/system/destructive actions | `/auto` |
| `/yolo` | Skip tool confirmations; use carefully | `/yolo` |
| `/plan` | Toggle Plan Mode | `/plan` |
| `/think` | Toggle Thinking Mode | `/think` |
| `/skills` | List loaded built-in tools and custom skills | `/skills` |
| `/new [name]` | Create and switch to a new persisted session | `/new Refactor auth` |
| `/sessions` | Switch saved sessions | `/sessions` |
| `/clear` | Clear the current session without deleting its file | `/clear` |
| `/undo` | Undo the latest user turn and following response | `/undo` |
| `/compress` | Manually compress older context | `/compress` |
| `/workspace` | Show the current workspace | `/workspace` |
| `/workspace move <path>` | Hot-switch workspace without restarting | `/workspace move C:\repo\app` |

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

Kairo reads selectable model profiles from `config.json` under `llm.providers[].models[]`. Use `/model` to choose one. After switching:

- model, base URL, temperature, max tokens, and context window update immediately;
- the Dock recalculates the context limit;
- session runtime state records the active profile.

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

Check `llm.providers`, provider names, model names, `active_provider`, and `active_model`.

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
