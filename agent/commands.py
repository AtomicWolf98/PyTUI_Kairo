from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

from agent.workspace_context import WorkspaceMoveError


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
        "name": "/plan",
        "summary": "Toggle Plan Mode",
        "help": "Toggle Plan Mode (agent drafts a plan before starting)",
    },
    {
        "name": "/manual",
        "summary": "Set MANUAL authorization",
        "help": "Set authorization level to MANUAL (confirm every tool)",
    },
    {
        "name": "/auto",
        "summary": "Set AUTO authorization",
        "help": "Set authorization level to AUTO (workspace-internal tools run automatically)",
    },
    {
        "name": "/yolo",
        "summary": "Set YOLO authorization",
        "help": "Set authorization level to YOLO (run all tools without confirmation)",
    },
    {
        "name": "/think",
        "summary": "Toggle Thinking Mode",
        "help": "Toggle Thinking Mode (display chain-of-thought)",
    },
    {
        "name": "/skills",
        "summary": "List tools and skills",
        "help": "List loaded custom and built-in skills",
    },
    {
        "name": "/clear",
        "summary": "Clear active conversation",
        "help": "Clear the conversation history",
    },
    {
        "name": "/compress",
        "summary": "Compress older context",
        "help": "Summarize older context while keeping recent turns",
    },
    {
        "name": "/new",
        "summary": "Create a conversation",
        "help": "Create and switch to a new in-memory conversation",
    },
    {
        "name": "/sessions",
        "summary": "Switch conversations",
        "help": "Switch between in-memory conversations",
    },
    {
        "name": "/config",
        "summary": "Show configuration",
        "help": "Show current settings",
    },
    {
        "name": "/model",
        "summary": "Select provider/model",
        "help": "Interactive menu to select the active provider and model",
    },
    {
        "name": "/undo",
        "summary": "Undo latest turn",
        "help": "Undo the last dialogue turn (user input and assistant response)",
    },
    {
        "name": "/workspace",
        "summary": "Workspace review / move",
        "help": "Show current workspace or use '/workspace move <path>' to switch",
    },
]


def get_command_map() -> Dict[str, str]:
    return {item["name"]: item["summary"] for item in COMMAND_CATALOG}


def build_help_markdown() -> str:
    lines = ["### Available Slash Commands", ""]
    for item in COMMAND_CATALOG:
        name = item["name"]
        if name == "/new":
            name = "/new [name]"
        lines.append(f"- `{name}` : {item['help']}")
    lines.append("")
    lines.append("### Keyboard Shortcuts")
    lines.append("")
    lines.append("- `Ctrl+B` : Toggle Workspace focus")
    lines.append("- `Ctrl+A` : Cycle authorization level (Manual → Auto → YOLO)")
    lines.append("- `Ctrl+P` : Toggle Plan Mode")
    lines.append("- `Ctrl+T` : Toggle Thinking Mode")
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
            "/quit": self._handle_exit,
            "/plan": self._handle_plan,
            "/manual": self._handle_manual,
            "/auto": self._handle_auto,
            "/yolo": self._handle_yolo,
            "/think": self._handle_think,
            "/skills": self._handle_skills,
            "/clear": self._handle_clear,
            "/new": self._handle_new,
            "/sessions": self._handle_sessions,
            "/config": self._handle_config,
            "/model": self._handle_model,
            "/compress": self._handle_compress,
            "/undo": self._handle_undo,
            "/workspace": self._handle_workspace,
        }

    def dispatch(self, raw: str) -> CommandResult:
        """Parse and execute a slash command, returning a structured result."""
        parts = raw.strip().split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        handler = self._handlers.get(command)
        if handler is None:
            return CommandResult(handled=False, success=False)
        return handler(raw, [command, argument])

    def _handle_help(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(
            handled=True,
            success=True,
            message=build_help_markdown(),
            data={"kind": "help"},
        )

    def _handle_exit(self, _raw: str, _parts: List[str]) -> CommandResult:
        return CommandResult(handled=True, success=True, exit_app=True)

    def _handle_plan(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.config.plan_mode = not self.agent.config.plan_mode
        self.agent.config.save()
        status = "ON" if self.agent.config.plan_mode else "OFF"
        return CommandResult(
            handled=True,
            success=True,
            message=f"Plan Mode is now {status} and config saved.",
            refresh_ui=True,
        )

    def _handle_manual(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.config.authorization_level = "manual"
        self.agent.config.save()
        return CommandResult(
            handled=True,
            success=True,
            message="Authorization level set to MANUAL. Every tool will ask for confirmation.",
            refresh_ui=True,
        )

    def _handle_auto(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.config.authorization_level = "auto"
        self.agent.config.save()
        return CommandResult(
            handled=True,
            success=True,
            message="Authorization level set to AUTO. Workspace-internal tools run automatically; external/system/destructive actions still require confirmation.",
            refresh_ui=True,
        )

    def _handle_yolo(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.config.authorization_level = "yolo"
        self.agent.config.save()
        return CommandResult(
            handled=True,
            success=True,
            message="Authorization level set to YOLO. All tools will execute automatically without confirmation. Use with extreme care.",
            refresh_ui=True,
        )

    def _handle_think(self, _raw: str, _parts: List[str]) -> CommandResult:
        self.agent.config.thinking_mode = not self.agent.config.thinking_mode
        self.agent.config.save()
        status = "ON" if self.agent.config.thinking_mode else "OFF"
        return CommandResult(
            handled=True,
            success=True,
            message=f"Thinking Mode is now {status} and config saved.",
            refresh_ui=True,
        )

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
        session = self.agent.conversations.create_session(name)
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

    def _handle_config(self, _raw: str, _parts: List[str]) -> CommandResult:
        cfg = self.agent.config
        text = (
            f"Model: {cfg.model}\n"
            f"Active Provider: {cfg.active_provider}\n"
            f"Active Model: {cfg.active_model}\n"
            f"Active Target: {cfg.active_model_profile}\n"
            f"Base URL: {cfg.base_url}\n"
            f"Temperature: {cfg.temperature}\n"
            f"Max Tokens: {cfg.max_tokens}\n"
            f"Context Window: {cfg.context_window}\n"
            f"Context Management: {cfg.context_management}\n"
            f"Active Conversation: {self.agent.active_session_name}\n"
            f"Configured Targets: {', '.join(cfg.get_model_profile_names())}\n"
            f"Auto Mode: {'ON' if cfg.auto_mode else 'OFF'}\n"
            f"Plan Mode: {'ON' if cfg.plan_mode else 'OFF'}\n"
            f"Thinking Mode: {'ON' if cfg.thinking_mode else 'OFF'}\n"
            f"Skills Directory: {cfg.skills_dir}\n"
            f"Workspace: {self.agent.workspace_context.root}\n"
        )
        return CommandResult(
            handled=True,
            success=True,
            message=text,
            data={"kind": "config"},
        )

    def _handle_model(self, _raw: str, _parts: List[str]) -> CommandResult:
        profiles = self.agent.config.get_model_profile_names()
        if not profiles:
            return CommandResult(
                handled=True,
                success=False,
                message="No model profiles configured.",
            )
        default_idx = 0
        if self.agent.config.active_model_profile in profiles:
            default_idx = profiles.index(self.agent.config.active_model_profile)
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "model", "profiles": profiles, "default_index": default_idx},
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
            self.agent.history = self.agent.history[:user_idx]
            return CommandResult(
                handled=True,
                success=True,
                message=f"Undid last conversation turn. Removed user message: \"{undone}\"",
                refresh_ui=True,
                data={"kind": "undo"},
            )
        return CommandResult(
            handled=True,
            success=False,
            message="No conversation turn to undo.",
            data={"kind": "undo"},
        )

    def _handle_workspace(self, raw: str, parts: List[str]) -> CommandResult:
        argument = parts[1] if len(parts) > 1 else ""
        if argument.lower().startswith("move "):
            target = argument[4:].strip()
            target_path = Path(target).expanduser().resolve()
            try:
                self.agent.workspace_context.move(target_path)
            except WorkspaceMoveError as exc:
                return CommandResult(
                    handled=True,
                    success=False,
                    message=f"Workspace move failed: {exc}",
                )
            except Exception as exc:
                return CommandResult(
                    handled=True,
                    success=False,
                    message=f"Workspace move failed: {exc}",
                )

            self.agent.config.workspace_root = str(target_path)
            self.agent.config.save()
            if self.agent.workspace_changed:
                self.agent.workspace_changed(str(target_path))
            return CommandResult(
                handled=True,
                success=True,
                message=f"Workspace moved to: {target_path}",
                refresh_ui=True,
                data={"kind": "workspace_moved", "root": str(target_path)},
            )

        return CommandResult(
            handled=True,
            success=True,
            message=(
                f"Current workspace: {self.agent.workspace_context.root}\n"
                "Use '/workspace move <path>' to switch to another directory."
            ),
            data={"kind": "workspace_show", "root": str(self.agent.workspace_context.root)},
        )
