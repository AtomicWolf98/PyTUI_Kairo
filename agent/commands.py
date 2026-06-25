from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

COMMAND_CATALOG: List[Dict[str, str]] = [
    {
        "name": "/help",
        "summary": "Show commands",
        "help": "Show this help message",
    },
    {
        "name": "/exit",
        "summary": "Exit Kairo",
        "help": "Exit the program",
    },
    {
        "name": "/new",
        "summary": "Create a conversation",
        "help": "Create and switch to a new persisted conversation",
    },
    {
        "name": "/sessions",
        "summary": "Manage conversations",
        "help": "Switch between persisted conversations",
    },
    {
        "name": "/clear",
        "summary": "Clear active conversation",
        "help": "Clear the conversation history",
    },
    {
        "name": "/undo",
        "summary": "Undo latest turn",
        "help": "Undo the last dialogue turn (user input and assistant response)",
    },
    {
        "name": "/compress",
        "summary": "Compress older context",
        "help": "Summarize older context while keeping recent turns",
    },
    {
        "name": "/model",
        "summary": "Switch chat profile",
        "help": "Switch chat profile",
    },
    {
        "name": "/setup",
        "summary": "First-time setup wizard",
        "help": "Run first-time setup wizard",
    },
    {
        "name": "/settings",
        "summary": "Manage configuration",
        "help": "Manage providers, models, keys, roles and config",
    },
    {
        "name": "/mode",
        "summary": "Switch mode",
        "help": "Switch Plan, Thinking, or Authorization mode",
    },
    {
        "name": "/workspace",
        "summary": "Workspace panel / switch",
        "help": "Open workspace panel or switch to path/bookmark",
    },
    {
        "name": "/status",
        "summary": "Show runtime status",
        "help": "Show read-only runtime status",
    },
    {
        "name": "/find",
        "summary": "Search sessions",
        "help": "Search session names and history content",
    },
    {
        "name": "/export",
        "summary": "Export session or config",
        "help": "Export session or config",
    },
    {
        "name": "/doctor",
        "summary": "Health dashboard",
        "help": "Check config, keys, workspace, sessions and provider health",
    },
    {
        "name": "/skills",
        "summary": "List tools and skills",
        "help": "List loaded custom and built-in skills",
    },
    {
        "name": "/docs",
        "summary": "Show docs index",
        "help": "Show local documentation index",
    },
]


_HELP_GROUP_MAP = {
    "/help": "Core",
    "/exit": "Core",
    "/doctor": "Core",
    "/new": "Conversation",
    "/sessions": "Conversation",
    "/clear": "Conversation",
    "/undo": "Conversation",
    "/compress": "Conversation",
    "/find": "Conversation",
    "/export": "Conversation",
    "/model": "Model & Config",
    "/setup": "Model & Config",
    "/settings": "Model & Config",
    "/mode": "Model & Config",
    "/status": "Model & Config",
    "/workspace": "Workspace",
    "/skills": "Tools & Docs",
    "/docs": "Tools & Docs",
}


REMOVED_COMMAND_HINTS = {
    "/manual": "Use /mode to change Plan, Thinking, or Authorization mode.",
    "/auto": "Use /mode to change Plan, Thinking, or Authorization mode.",
    "/yolo": "Use /mode to change Plan, Thinking, or Authorization mode.",
    "/plan": "Use /mode to change Plan, Thinking, or Authorization mode.",
    "/think": "Use /mode to change Plan, Thinking, or Authorization mode.",
    "/provider": "Use /settings > Providers.",
    "/providers": "Use /settings > Providers.",
    "/key": "Use /settings > Keys.",
    "/keys": "Use /settings > Keys.",
    "/role": "Use /settings > Roles.",
    "/roles": "Use /settings > Roles.",
    "/config": "Use /settings > Config or /export for exports.",
    "/session": "Use /sessions.",
    "/workspaces": "Use /workspace.",
}


def get_command_map() -> Dict[str, str]:
    return {item["name"]: item["summary"] for item in COMMAND_CATALOG}


def build_help_markdown() -> str:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for item in COMMAND_CATALOG:
        group = _HELP_GROUP_MAP.get(item["name"], "Other")
        groups.setdefault(group, []).append(item)

    group_order = [
        "Core",
        "Conversation",
        "Model & Config",
        "Workspace",
        "Tools & Docs",
    ]

    lines: List[str] = []
    lines.append("### Available Slash Commands")
    lines.append("")
    for group in group_order:
        if group not in groups:
            continue
        lines.append(f"### {group}")
        lines.append("")
        for item in groups[group]:
            name = item["name"]
            if name == "/new":
                name = "/new [name]"
            elif name == "/find":
                name = "/find <keyword>"
            elif name == "/workspace":
                name = "/workspace [path-or-bookmark]"
            lines.append(f"- `{name}` : {item['help']}")
        lines.append("")

    lines.append("### Keyboard Shortcuts")
    lines.append("")
    lines.append("- `Ctrl+B` : Toggle Workspace focus")
    lines.append("- `Ctrl+A` : Cycle authorization level (Manual -> Auto -> YOLO)")
    lines.append("- `Ctrl+P` : Toggle Plan Mode")
    lines.append("- `Ctrl+T` : Toggle Thinking Mode")
    lines.append("- `Esc` : Stop the current generation (Textual mode)")
    lines.append("")
    return "\n".join(lines)


@dataclass
class CommandResult:
    """Structured result returned by the slash-command dispatcher."""

    handled: bool
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    refresh_ui: bool = False
    exit_app: bool = False
    interactive: bool = False


class CommandDispatcher:
    """Single dispatcher for slash commands shared by plain and Textual UIs."""

    def __init__(self, agent):
        self.agent = agent
        self._handlers: Dict[str, Callable[[str, List[str]], CommandResult]] = {
            "/help": self._handle_help,
            "/exit": self._handle_exit,
            "/skills": self._handle_skills,
            "/clear": self._handle_clear,
            "/new": self._handle_new,
            "/sessions": self._handle_sessions,
            "/model": self._handle_model,
            "/compress": self._handle_compress,
            "/undo": self._handle_undo,
            "/workspace": self._handle_workspace,
            "/doctor": self._handle_doctor,
            "/setup": self._handle_setup,
            "/mode": self._handle_mode,
            "/status": self._handle_status,
            "/find": self._handle_find,
            "/export": self._handle_export,
            "/settings": self._handle_settings,
            "/docs": self._handle_docs,
        }

    def dispatch(self, raw: str) -> CommandResult:
        """Parse and execute a slash command, returning a structured result."""
        raw_stripped = raw.strip()
        if not raw_stripped:
            return CommandResult(handled=False, success=False)

        parts = raw_stripped.split(maxsplit=1)
        command = parts[0].lower()

        # 1. Exact new command first.
        handler = self._handlers.get(command)
        if handler is not None:
            return handler(raw_stripped, parts)

        # 2. Removed prefix.
        if command in REMOVED_COMMAND_HINTS:
            return CommandResult(
                handled=True,
                success=False,
                message=f"This command was removed in 0.2.7-beta. {REMOVED_COMMAND_HINTS[command]}",
                data={"kind": "removed_command"},
            )

        # 3. Unknown command.
        return CommandResult(handled=False, success=False)

    def _handle_help(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            message=build_help_markdown(),
            data={"kind": "help"},
        )

    def _handle_exit(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(handled=True, success=True, exit_app=True)

    def _handle_skills(self, _raw: str, _parts: List[str]) -> CommandResult:
        skills = [
            {"name": name, "description": tool.description}
            for name, tool in self.agent.registry.tools.items()
        ]
        return CommandResult(
            handled=True,
            success=True,
            data={"kind": "skills", "skills": skills},
        )

    def _handle_clear(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.reset_history()
        return CommandResult(
            handled=True,
            success=True,
            message="Conversation history cleared.",
            refresh_ui=True,
            data={"kind": "clear"},
        )

    def _handle_new(self, raw: str, parts: List[str]) -> CommandResult:
        name = raw.strip()[len(parts[0]):].strip() or None
        try:
            session = self.agent.conversations.create_session(name)
        except RuntimeError as exc:
            return CommandResult(
                handled=True,
                success=False,
                message=str(exc),
                data={"kind": "new"},
            )
        return CommandResult(
            handled=True,
            success=True,
            message=f"Created conversation: {session.name}",
            data={"kind": "new", "session": session},
            refresh_ui=True,
        )

    def _handle_sessions(self, _raw: str, _parts: List[str]) -> CommandResult:
        options = self.agent.conversations.session_menu_options()
        current_idx = next(
            (index for index, session in enumerate(self.agent.conversations.sessions)
             if session.id == self.agent.conversations.active_session_id),
            0,
        )
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "sessions", "options": options, "default_index": current_idx},
        )

    def _handle_model(self, _raw: str, parts: List[str]) -> CommandResult:
        arg = parts[1].strip() if len(parts) > 1 else ""
        if arg.lower() in ("add", "edit", "remove", "test"):
            return CommandResult(
                handled=True,
                success=False,
                message=(
                    "This command was removed in 0.2.7-beta. "
                    "Use /settings > Models. Use /model only to switch chat profile."
                ),
                data={"kind": "removed_command"},
            )

        cfg = self.agent.config
        from agent.profile_resolver import get_active_profile

        if cfg.llm.get("profiles"):
            profiles = cfg.get_profile_ids()
            if not profiles:
                return CommandResult(
                    handled=True,
                    success=False,
                    message="No profiles configured.",
                )
            resolved = get_active_profile(cfg)
            active_id = (resolved.id if resolved else "") or cfg.llm.get("active_profile") or cfg.active_model_profile
            default_idx = profiles.index(active_id) if active_id in profiles else 0
            role_override = bool(cfg.model_roles.get("chat"))
            return CommandResult(
                handled=True,
                success=True,
                interactive=True,
                data={
                    "kind": "model",
                    "profiles": profiles,
                    "default_index": default_idx,
                    "mode": "profile",
                    "role_override": role_override,
                },
            )

        profiles = cfg.get_model_profile_names()
        if not profiles:
            return CommandResult(
                handled=True,
                success=False,
                message="No model profiles configured.",
            )
        resolved = get_active_profile(cfg)
        active_label = (resolved.label if resolved and resolved.label else "") or cfg.active_model_profile
        default_idx = profiles.index(active_label) if active_label in profiles else 0
        role_override = bool(cfg.model_roles.get("chat"))
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={
                "kind": "model",
                "profiles": profiles,
                "default_index": default_idx,
                "mode": "legacy",
                "role_override": role_override,
            },
        )

    def _handle_compress(self, _raw: str, _parts: List[str]) -> CommandResult:
        success, message = self.agent.compress_context(manual=True)
        return CommandResult(
            handled=True,
            success=success,
            message=message,
            refresh_ui=True,
            data={"kind": "compress"},
        )

    def _handle_undo(self, _raw: str, _parts: List[str]) -> CommandResult:
        user_idx = -1
        for i in range(len(self.agent.history) - 1, -1, -1):
            if self.agent.history[i]["role"] == "user":
                user_idx = i
                break
        if user_idx != -1:
            undone = self.agent.history[user_idx]["content"]
            self.agent.conversations.replace_active_history(
                self.agent.history[:user_idx],
                reason="undo",
                save=True,
            )
            return CommandResult(
                handled=True,
                success=True,
                message=f'Undid last conversation turn. Removed user message: "{undone}"',
                refresh_ui=True,
                data={"kind": "undo"},
            )
        return CommandResult(
            handled=True,
            success=False,
            message="No conversation turn to undo.",
            data={"kind": "undo"},
        )

    def _handle_workspace(self, _raw: str, parts: List[str]) -> CommandResult:
        arg = parts[1].strip() if len(parts) > 1 else ""
        first = arg.split(None, 1)[0].lower() if arg else ""
        if first in ("save", "remove", "move"):
            return CommandResult(
                handled=True,
                success=False,
                message="This command was removed in 0.2.7-beta. Use /workspace.",
                data={"kind": "removed_command"},
            )

        if not arg:
            return CommandResult(
                handled=True,
                success=True,
                interactive=True,
                data={"kind": "workspace"},
            )

        from agent.runtime_commands import _resolve_workspace_target
        resolved = _resolve_workspace_target(self.agent.config, arg)
        if resolved is None:
            return CommandResult(handled=True, success=False, message="Workspace target is required.")
        return self.agent.move_workspace(resolved)

    def _handle_doctor(self, raw: str, parts: List[str]) -> CommandResult:
        from agent import runtime_commands as rc
        return rc.handle_doctor(self.agent, raw, parts)

    def _handle_setup(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "setup"},
        )

    def _handle_mode(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "mode"},
        )

    def _handle_status(self, _raw: str, _parts: List[str]) -> CommandResult:
        cfg = self.agent.config
        from agent.profile_resolver import get_active_profile, list_profiles, mask_key

        active = get_active_profile(cfg)
        key_hint = cfg.describe_active_api_key()
        profiles_text = "\n".join(
            f"  - {p.id} ({p.model})  key={mask_key(p.api_key)}"
            for p in list_profiles(cfg)
        )
        roles_text = "\n".join(
            f"  - {role}: {target}"
            for role, target in cfg.model_roles.items()
        ) or "  (none configured)"
        bookmarks_text = "\n".join(
            f"  - {b['name']}: {b['path']}"
            for b in cfg.workspace_bookmarks
        ) or "  (none configured)"
        text = (
            f"Active Profile: {active.id if active else 'none'}\n"
            f"Model: {cfg.model}\n"
            f"Base URL: {cfg.base_url}\n"
            f"Temperature: {cfg.temperature}\n"
            f"Max Tokens: {cfg.max_tokens}\n"
            f"Context Window: {cfg.context_window}\n"
            f"Context Management: {cfg.context_management}\n"
            f"{key_hint}\n"
            f"Active Conversation: {self.agent.active_session_name}\n"
            f"Messages: {len(self.agent.history)}\n"
            f"\nProfiles:\n{profiles_text}\n"
            f"\nModel Roles:\n{roles_text}\n"
            f"\nWorkspace Bookmarks:\n{bookmarks_text}\n"
            f"\nAuto Mode: {'ON' if cfg.auto_mode else 'OFF'}"
            f"  Plan Mode: {'ON' if cfg.plan_mode else 'OFF'}"
            f"  Thinking Mode: {'ON' if cfg.thinking_mode else 'OFF'}\n"
            f"Skills Directory: {cfg.skills_dir}\n"
            f"Workspace: {self.agent.workspace_context.root}\n"
            f"\nManage profiles, keys, roles and config via '/settings'."
        )
        return CommandResult(
            handled=True,
            success=True,
            message=text,
            data={"kind": "status"},
        )

    def _handle_find(self, _raw: str, parts: List[str]) -> CommandResult:
        keyword = parts[1].strip() if len(parts) > 1 else ""
        if not keyword:
            return CommandResult(
                handled=True,
                success=False,
                message="Usage: /find <keyword>",
                data={"kind": "find"},
            )
        from agent import runtime_commands as rc
        results = rc._search_sessions(self.agent, keyword)
        if not results:
            return CommandResult(
                handled=True,
                success=True,
                message=f"No sessions matched '{keyword}'.",
                data={"kind": "find", "results": []},
            )
        lines = [f"[{r['index']}] {r['name']}  ({r['path']})" for r in results]
        return CommandResult(
            handled=True,
            success=True,
            message="\n".join(lines),
            data={"kind": "find", "results": results},
        )

    def _handle_export(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "export"},
        )

    def _handle_settings(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "settings"},
        )

    def _handle_docs(self, raw: str, parts: List[str]) -> CommandResult:
        topic = parts[1].strip() if len(parts) > 1 else ""
        first = topic.split(None, 1)[0].lower() if topic else ""
        if first in ("config", "providers", "sessions"):
            return CommandResult(
                handled=True,
                success=False,
                message="This command was removed in 0.2.7-beta. Use /docs.",
                data={"kind": "removed_command"},
            )
        from agent import runtime_commands as rc
        return rc.handle_docs(self.agent, raw, parts)
