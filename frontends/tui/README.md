# kairo-tui

The Textual frontend uses a chat-first shell inspired by modern agent TUIs:
the conversation and composer stay on screen, while sessions, workspace,
settings, extensions, memory, and diagnostics open from the searchable command
palette (`Ctrl+P`) or a modal. `Ctrl+X` is the leader key (`B` toggles the
optional context drawer). See `../../docs/tui-redesign.md` for the complete
interaction and responsive layout contract.

Version **0.4.0a2** (depends on `kairo-kernel==0.4.0a2`). Launch directly, or
via the legacy `kairo --tui` compat entry.

Textual-based terminal user interface for the Kairo agent. Provides an
interactive workspace console over the `kairo_kernel` public API (`kairo` or `kairo-tui`
[WORKSPACE]`, with `--config`, `--theme`, `--reduced-motion`, `--safe-mode`,
and `--headless-smoke` options).
