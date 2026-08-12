# kairo-tui (V2)

Chat-first Textual frontend for the Kairo agent.

## Layout

- `kairo_tui/app.py` — the chat-first workbench (TopBar / Transcript / Composer / StatusLine)
- `kairo_tui/controller.py` — kernel-facing intents; never imports Textual
- `kairo_tui/state.py` + `reducer.py` — immutable, replayable UI state
- `kairo_tui/event_loop.py` — kernel event consumption (replay → live, gap/overflow recovery)
- `kairo_tui/dialogs/` — connect, commands, sessions, models, approval, plan, confirm
- `kairo_tui/panels/` — context, workspace, settings, memory, extensions, diagnostics sidebars
- `kairo_tui/widgets/` — shell, transcript, composer, message, tool/plan cards, status

## Running

```powershell
python -m kairo_tui
```

Headless smoke gate (used by installers and CI):

```powershell
python -m kairo_tui --headless-smoke
```

## Development

```powershell
python -m pytest tests
python -m ruff check kairo_tui tests
python -m mypy kairo_tui
```
