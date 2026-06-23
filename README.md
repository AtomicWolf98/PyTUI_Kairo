# Kairo

Kairo is a terminal-native coding agent with an animated Textual TUI and a plain terminal fallback. It connects to OpenAI-compatible providers and can work with local files, shell commands, Python execution, web fetching, workspace review, custom skills, context compression, and persisted conversations.

Current release: **0.2.2**

## Highlights

- **Animated TUI and plain mode**: starts in the Textual interface by default; use `--plain` for compatible terminal output or automation.
- **Workspace review**: right-side Dock shows the file tree, changed files, read-only diffs, model/session state, and context usage.
- **Hot workspace switching**: `/workspace move <path>` updates tools, Dock, shell working directory, and current conversation runtime state without restarting.
- **Persisted sessions**: `/new` and `/sessions` conversations are saved independently under `sessions.storage_dir` in `config.json`.
- **Context management**: automatic and manual compression with `/compress`.
- **Authorization levels**: `manual`, `auto`, and `yolo` control tool confirmation behavior.
- **Model profiles**: choose from configured provider/model profiles with `/model`.
- **Multiline composer**: `Shift+Enter` or `Ctrl+Enter` inserts a newline; the input area grows up to eight visible lines.

## Install

### Windows

```powershell
.\run.bat
```

Or manually:

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

## First Configuration

```powershell
Copy-Item config.example.json config.json
$env:KAIRO_DEEPSEEK_API_KEY = "your-api-key"
kairo
```

Prefer `api_key_env` in `config.json` so API keys stay in environment variables instead of being written to disk.

## Common Commands

| Command | Purpose |
| --- | --- |
| `/help` | Show command help |
| `/model` | Select configured provider/model profile |
| `/new [name]` | Create and switch to a new persisted session |
| `/sessions` | Switch sessions |
| `/compress` | Compress older context |
| `/workspace` | Show current workspace |
| `/workspace move <path>` | Switch workspace without restarting |
| `/manual` `/auto` `/yolo` | Change authorization level |
| `/plan` | Toggle Plan Mode |
| `/think` | Toggle Thinking Mode |
| `/clear` | Clear current session |
| `/undo` | Undo latest conversation turn |
| `/exit` | Exit Kairo |

## Session Storage

By default, sessions are saved under `.kairo/sessions` relative to `config.json`.

```json
"sessions": {
  "enabled": true,
  "storage_dir": ".kairo/sessions",
  "autosave": true,
  "save_interval_seconds": 1.0,
  "max_sessions": 200
}
```

Session files may contain prompts, code, file contents, command output, and other sensitive information. The default `.kairo/` path is ignored by Git.

## Useful Docs

- [User Guide](docs/user-guide.md)
- [Configuration](docs/configuration.md)
- [Commands](docs/commands.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)
