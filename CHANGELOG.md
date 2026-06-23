# Changelog

## [0.2.2]

### Added

- Persisted conversation sessions with configurable `sessions.storage_dir`.
- Independent session files plus `index.json` for restoring the active session after restart.
- Autosave after session creation, switching, clearing, undo, compression, model changes, workspace changes, and completed interactions.
- Runtime state system message so the active conversation sees workspace/model changes immediately.
- Workspace hot-switch coverage for current conversation state, shell cwd, tool policy roots, search path rendering, and Textual Dock refresh.
- Tests for session storage and workspace hot-switch behavior.

### Changed

- `/new` and `/sessions` now operate on persisted sessions when session storage is enabled.
- `/workspace move <path>` is treated as a current-process state transition, not a setting that requires restart.
- User docs now describe persisted sessions and the 0.2.2 workspace hot-switch behavior.

### Fixed

- Search results now use the active workspace root as the relative-path base after workspace moves.
- Workspace moves update the conversation runtime state so the next model request does not rely on stale path context.
- Shell execution restarts in the new workspace after `/workspace move`.

## [0.2.1]

### Added

- Three authorization levels: `manual`, `auto`, and `yolo`.
- `/workspace move <path>` command.
- Slash command palette with keyboard selection and scrolling.
- Workspace Dock with file tree, changed files, and read-only diff review.
- Responsive Dock width, context progress bar, Kai animation, Textual TUI, and plain fallback.
- Provider/model profile configuration under `llm.providers`.
- Context management commands and in-process multi-session support.
- Multiline composer with `Shift+Enter` and `Ctrl+Enter`.

### Fixed

- `/config` no longer crashes the Textual app from the UI thread.
- Workspace tree refreshes correctly when switching between directories with identical structures.
- Fast workspace switches no longer allow stale scans to overwrite the latest Dock state.
- Wide Markdown/table content renders without clipping normal message text.

## [0.2.0]

### Added

- Kairo branding, Kai terminal mascot, animated Textual TUI, responsive Dock, and plain fallback.
- Model profiles, context management, `/compress`, `/new`, and `/sessions`.

## [0.1.0]

### Added

- pyTUI prototype with Rich CLI, OpenAI-compatible streaming client, and basic local tools.
