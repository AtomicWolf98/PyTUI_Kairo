"""TUI-side commands: navigation, help, exit. Business commands stay in the kernel.

``build_command_palette`` merges the TUI registry with the kernel command
catalog (TUI entries win on name clash), so the command palette and the slash
input resolve exactly the same commands (tui_plan.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.commands import ParsedCommand
from kairo_kernel.contracts.identifiers import SessionId

from kairo_tui.store import AppState, PageAction, PageId

TUI_COMMANDS: dict[str, str] = {
    "/help": "Show this help.",
    "/chat": "Open the Chat page.",
    "/sessions": "Open Sessions.",
    "/workspace": "Open Workspace.",
    "/memory": "Open Memory.",
    "/extensions": "Open Extensions.",
    "/settings": "Open Settings.",
    "/doctor": "Open Doctor.",
    "/setup": "Open Setup.",
    "/exit": "Exit Kairo (confirmation flow when turns are running).",
}
PAGE_BY_COMMAND = {
    "/chat": PageId.CHAT, "/sessions": PageId.SESSIONS, "/workspace": PageId.WORKSPACE,
    "/memory": PageId.MEMORY, "/extensions": PageId.EXTENSIONS, "/settings": PageId.SETTINGS,
    "/doctor": PageId.DOCTOR, "/setup": PageId.SETUP,
}

# Words that double as kernel business commands: bare → nav, with args → kernel.
ARG_AWARE_COMMANDS = frozenset({"/workspace", "/memory", "/doctor"})


def parse_tui_command(text: str) -> ParsedCommand | None:
    """Return a ParsedCommand when the text names a TUI command, else None.

    Arg-aware: /workspace|/memory|/doctor with arguments fall through to the
    kernel business commands (workspace move / memory search / diagnostics).
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    tokens = stripped.split()
    name = tokens[0]
    if name not in TUI_COMMANDS:
        return None
    if name in ARG_AWARE_COMMANDS and len(tokens) > 1:
        return None
    return ParsedCommand(name, tuple(tokens[1:]))


async def execute_tui_command(app, parsed: ParsedCommand) -> bool:
    """Execute a TUI command; return True when handled."""
    name = parsed.name
    if name in PAGE_BY_COMMAND:
        page = PAGE_BY_COMMAND[name]
        if page is PageId.CHAT:
            if getattr(app, "_modal_page", None) is not None and len(app.screen_stack) > 1:
                app.pop_screen()
            app.store.dispatch(PageAction(page))
            app._show_page(page)
        else:
            app.open_management(page)
        return True
    if name == "/help":
        app.notify("\n".join(f"{k}: {v}" for k, v in TUI_COMMANDS.items()))
        return True
    if name == "/exit":
        await app.request_exit()
        return True
    return False


@dataclass(frozen=True)
class PaletteEntry:
    """One selectable palette command: TUI nav or a kernel business command."""

    name: str
    summary: str
    kernel: bool = False
    help: str = ""  # kernel catalog help text; TUI entries fall back to the summary


def build_command_palette(app) -> tuple[PaletteEntry, ...]:
    """Merge ``TUI_COMMANDS`` with the kernel catalog for the command palette.

    TUI entries win on name clash (``/sessions``, ``/workspace``, ``/memory``
    and ``/doctor`` are TUI nav), so palette and slash input resolve the same
    commands; the remaining kernel business commands follow in catalog order.
    """
    entries = [PaletteEntry(name, summary) for name, summary in TUI_COMMANDS.items()]
    names = set(TUI_COMMANDS)
    for command in app.kernel.commands.catalog():
        if command.name not in names:
            entries.append(PaletteEntry(command.name, command.summary, kernel=True, help=command.help))
    return tuple(entries)


def active_session_contract(state: AppState) -> SessionId | None:
    """The store's active session as a kernel ``SessionId``, or None when inactive."""
    session_id = state.active_session_id
    return SessionId(session_id) if session_id else None
