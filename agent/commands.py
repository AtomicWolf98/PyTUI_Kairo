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
        "help": "Create and switch to a new persisted conversation",
    },
    {
        "name": "/sessions",
        "summary": "Switch conversations",
        "help": "Switch between persisted conversations",
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
    {
        "name": "/providers",
        "summary": "List providers",
        "help": "List every configured LLM provider",
    },
    {
        "name": "/provider add",
        "summary": "Add provider wizard",
        "help": "Interactive wizard to add a new OpenAI-compatible provider",
    },
    {
        "name": "/provider edit",
        "summary": "Edit provider",
        "help": "Edit an existing provider (URL / API key / name)",
    },
    {
        "name": "/provider remove",
        "summary": "Remove provider",
        "help": "Remove a provider and its models (with confirmation)",
    },
    {
        "name": "/provider test",
        "summary": "Test provider",
        "help": "Send a minimal probe to validate provider reachability",
    },
    {
        "name": "/model add",
        "summary": "Add model",
        "help": "Add a new model to an existing provider",
    },
    {
        "name": "/model edit",
        "summary": "Edit model",
        "help": "Edit an existing model's parameters",
    },
    {
        "name": "/model remove",
        "summary": "Remove model",
        "help": "Remove a model from its provider (with confirmation)",
    },
    {
        "name": "/model test",
        "summary": "Test model",
        "help": "Send a minimal probe to validate model availability",
    },
    {
        "name": "/settings",
        "summary": "Open settings",
        "help": "Open the settings menu (providers, models, modes)",
    },
    {
        "name": "/config validate",
        "summary": "Validate config",
        "help": "Validate the current configuration and show issues",
    },
    {
        "name": "/config backup",
        "summary": "Create config backup",
        "help": "Write a timestamped backup of config.json",
    },
    {
        "name": "/config restore",
        "summary": "Restore config backup",
        "help": "Restore a previously written config backup",
    },
    {
        "name": "/session rename",
        "summary": "Rename session",
        "help": "Rename the current session",
    },
    {
        "name": "/session delete",
        "summary": "Delete session",
        "help": "Delete a session (with confirmation)",
    },
    {
        "name": "/session export",
        "summary": "Export session",
        "help": "Export the current session as Markdown or JSON",
    },
    {
        "name": "/session reveal",
        "summary": "Show session path",
        "help": "Print the file path of the current session",
    },
    {
        "name": "/keys",
        "summary": "List API key statuses",
        "help": "List all profile key statuses (masked)",
    },
    {
        "name": "/key set",
        "summary": "Set profile API key",
        "help": "Set inline API key for a profile",
    },
    {
        "name": "/key clear",
        "summary": "Clear profile API key",
        "help": "Clear inline API key for a profile",
    },
    {
        "name": "/key reveal",
        "summary": "Reveal profile API key",
        "help": "Reveal full API key for a profile after confirmation",
    },
    {
        "name": "/key migrate",
        "summary": "Migrate legacy keys",
        "help": "Migrate legacy provider keys into profile keys",
    },
    {
        "name": "/roles",
        "summary": "List model roles",
        "help": "List current model role mappings",
    },
    {
        "name": "/role set",
        "summary": "Set model role",
        "help": "Set which profile a model role uses",
    },
    {
        "name": "/role clear",
        "summary": "Clear model role",
        "help": "Clear a model role mapping",
    },
    {
        "name": "/doctor",
        "summary": "Health dashboard",
        "help": "Check config, keys, workspace, sessions and provider health",
    },
    {
        "name": "/workspace save",
        "summary": "Save workspace bookmark",
        "help": "Save the current workspace root as a bookmark",
    },
    {
        "name": "/workspaces",
        "summary": "List workspace bookmarks",
        "help": "List saved workspace bookmarks",
    },
    {
        "name": "/workspace remove",
        "summary": "Remove workspace bookmark",
        "help": "Remove a workspace bookmark by name",
    },
    {
        "name": "/session search",
        "summary": "Search sessions",
        "help": "Search session names and history content",
    },
    {
        "name": "/session open",
        "summary": "Open session",
        "help": "Open a session by id or search result index",
    },
    {
        "name": "/config export",
        "summary": "Export config",
        "help": "Export config to .kairo/config_exports (redacted by default)",
    },
    {
        "name": "/config import",
        "summary": "Import config",
        "help": "Import config from a JSON file",
    },
    {
        "name": "/docs config",
        "summary": "Show docs: configuration",
        "help": "Show the path of the configuration doc",
    },
    {
        "name": "/docs providers",
        "summary": "Show docs: providers",
        "help": "Show the path of the provider doc",
    },
    {
        "name": "/docs sessions",
        "summary": "Show docs: sessions",
        "help": "Show the path of the session doc",
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
            "/workspaces": self._handle_workspaces,
            "/keys": self._handle_keys,
            "/key": self._dispatch_key,
            "/roles": self._handle_roles,
            "/role": self._dispatch_role,
            "/doctor": self._handle_doctor,
        }
        self._register_runtime_handlers()

    def _register_runtime_handlers(self) -> None:
        # Import lazily to avoid circular imports at module load time.
        from agent import runtime_commands as rc

        def bind(fn):
            return lambda raw, parts: fn(self.agent, raw, parts)

        self._handlers.update({
            "/providers": bind(rc.handle_providers),
            "/provider": self._dispatch_provider,
            "/settings": bind(rc.handle_settings),
            "/session": self._dispatch_session,
            "/docs": bind(rc.handle_docs),
        })
        # /model add|edit|remove|test collide with the existing /model handler;
        # resolved in dispatch() based on whether an argument is present.
        self._model_runtime = {
            "add": rc.handle_model_add,
            "edit": rc.handle_model_edit,
            "remove": rc.handle_model_remove,
            "test": rc.handle_model_test,
        }
        self._provider_runtime = {
            "add": rc.handle_provider_add,
            "edit": rc.handle_provider_edit,
            "remove": rc.handle_provider_remove,
            "test": rc.handle_provider_test,
        }
        self._session_runtime = {
            "rename": rc.handle_session_rename,
            "delete": rc.handle_session_delete,
            "export": rc.handle_session_export,
            "reveal": rc.handle_session_reveal,
            "search": rc.handle_session_search,
            "open": rc.handle_session_open,
        }
        self._config_runtime = {
            "validate": rc.handle_config_validate,
            "backup": rc.handle_config_backup,
            "restore": rc.handle_config_restore,
            "export": rc.handle_config_export,
            "import": rc.handle_config_import,
        }
        self._key_runtime = {
            "set": rc.handle_key_set,
            "clear": rc.handle_key_clear,
            "reveal": rc.handle_key_reveal,
            "migrate": rc.handle_key_migrate,
        }
        self._role_runtime = {
            "set": rc.handle_role_set,
            "clear": rc.handle_role_clear,
        }

    def _dispatch_provider(self, raw: str, parts: List[str]) -> CommandResult:
        sub = (parts[1] if len(parts) > 1 else "").strip().lower()
        if sub in self._provider_runtime:
            return self._provider_runtime[sub](self.agent, raw, parts)
        # "/provider" alone is treated as "/providers".
        from agent import runtime_commands as rc

        return rc.handle_providers(self.agent, raw, parts)

    def _dispatch_session(self, raw: str, parts: List[str]) -> CommandResult:
        sub = (parts[1] if len(parts) > 1 else "").strip().lower()
        if sub in self._session_runtime:
            return self._session_runtime[sub](self.agent, raw, parts)
        return CommandResult(handled=False, success=False, message="Use /session rename|delete|export|reveal|search|open.")

    def _dispatch_key(self, raw: str, parts: List[str]) -> CommandResult:
        sub = (parts[1] if len(parts) > 1 else "").strip().lower()
        if sub in self._key_runtime:
            return self._key_runtime[sub](self.agent, raw, parts)
        return CommandResult(handled=False, success=False, message="Use /key set|clear|reveal|migrate.")

    def _dispatch_role(self, raw: str, parts: List[str]) -> CommandResult:
        sub = (parts[1] if len(parts) > 1 else "").strip().lower()
        if sub in self._role_runtime:
            return self._role_runtime[sub](self.agent, raw, parts)
        return CommandResult(handled=False, success=False, message="Use /role set|clear.")

    def _dispatch_config(self, raw: str, parts: List[str]) -> CommandResult:
        sub = (parts[1] if len(parts) > 1 else "").strip().lower()
        if sub in self._config_runtime:
            return self._config_runtime[sub](self.agent, raw, parts)
        return self._handle_config(raw, parts)

    def dispatch(self, raw: str) -> CommandResult:
        """Parse and execute a slash command, returning a structured result."""
        raw_stripped = raw.strip()
        parts = raw_stripped.split(maxsplit=2)
        command = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""
        argument = parts[2].strip() if len(parts) > 2 else ""

        # Multi-word commands first.
        if command == "/model" and sub in self._model_runtime:
            handler = self._model_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/provider" and sub in self._provider_runtime:
            handler = self._provider_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/session" and sub in self._session_runtime:
            handler = self._session_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/key" and sub in self._key_runtime:
            handler = self._key_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/role" and sub in self._role_runtime:
            handler = self._role_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/config" and sub in self._config_runtime:
            handler = self._config_runtime[sub]
            return handler(self.agent, raw_stripped, [command, sub, argument])
        if command == "/workspace" and sub in ("save", "remove", "move"):
            handler = self._handlers.get("/workspace")
            return handler(raw_stripped, [command, sub, argument])
        if command == "/docs":
            # /docs can take a topic arg; collapse into single-arg form for handler.
            topic_arg = sub + (" " + argument if argument else "")
            # Use the registered bound handler so the agent is injected uniformly.
            return self._handlers["/docs"](raw_stripped, [command, topic_arg.strip()])

        handler = self._handlers.get(command)
        if handler is None:
            return CommandResult(handled=False, success=False)
        full_argument = (sub + (" " + argument if argument else "")).strip()
        return handler(raw_stripped, [command, full_argument])

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

    def _handle_config(self, _raw: str, _parts: List[str]) -> CommandResult:
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
            f"\nProfiles:\n{profiles_text}\n"
            f"\nModel Roles:\n{roles_text}\n"
            f"\nWorkspace Bookmarks:\n{bookmarks_text}\n"
            f"\nAuto Mode: {'ON' if cfg.auto_mode else 'OFF'}"
            f"  Plan Mode: {'ON' if cfg.plan_mode else 'OFF'}"
            f"  Thinking Mode: {'ON' if cfg.thinking_mode else 'OFF'}\n"
            f"Skills Directory: {cfg.skills_dir}\n"
            f"Workspace: {self.agent.workspace_context.root}\n"
            f"\nManage profiles/keys/roles via '/keys', '/key', '/roles', '/role', or '/settings'."
        )
        return CommandResult(
            handled=True,
            success=True,
            message=text,
            data={"kind": "config"},
        )

    def _handle_model(self, _raw: str, _parts: List[str]) -> CommandResult:
        cfg = self.agent.config
        if cfg.llm.get("profiles"):
            profiles = cfg.get_profile_ids()
            if not profiles:
                return CommandResult(
                    handled=True,
                    success=False,
                    message="No profiles configured.",
                )
            default_idx = 0
            active_id = cfg.llm.get("active_profile") or cfg.active_model_profile
            if active_id in profiles:
                default_idx = profiles.index(active_id)
            return CommandResult(
                handled=True,
                success=True,
                interactive=True,
                data={"kind": "model", "profiles": profiles, "default_index": default_idx, "mode": "profile"},
            )
        profiles = cfg.get_model_profile_names()
        if not profiles:
            return CommandResult(
                handled=True,
                success=False,
                message="No model profiles configured.",
            )
        default_idx = 0
        if cfg.active_model_profile in profiles:
            default_idx = profiles.index(cfg.active_model_profile)
        return CommandResult(
            handled=True,
            success=True,
            interactive=True,
            data={"kind": "model", "profiles": profiles, "default_index": default_idx, "mode": "legacy"},
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
            # 0.2.4: use replace_active_history with save=True to persist immediately
            self.agent.conversations.replace_active_history(
                self.agent.history[:user_idx],
                reason="undo",
                save=True,
            )
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
        sub = parts[1] if len(parts) > 1 else ""
        argument = parts[2] if len(parts) > 2 else ""
        lowered = sub.lower()
        if lowered == "move":
            target = argument.strip()
            if not target:
                return CommandResult(handled=True, success=False, message="Workspace target is required.")
            from agent.runtime_commands import _resolve_workspace_target
            resolved = _resolve_workspace_target(self.agent.config, target)
            if resolved is None:
                return CommandResult(handled=True, success=False, message="Workspace target is required.")
            return self.agent.move_workspace(resolved)
        if lowered == "save":
            from agent import runtime_commands as rc
            return rc.handle_workspace_save(self.agent, raw, parts)
        if lowered == "remove":
            from agent import runtime_commands as rc
            return rc.handle_workspace_remove(self.agent, raw, parts)

        return CommandResult(
            handled=True,
            success=True,
            message=(
                f"Current workspace: {self.agent.workspace_context.root}\n"
                "Use '/workspace move <path>' to switch, '/workspace save <name>' to bookmark, "
                "'/workspaces' to list bookmarks, '/workspace remove <name>' to delete."
            ),
            data={"kind": "workspace_show", "root": str(self.agent.workspace_context.root)},
        )

    def _handle_workspaces(self, raw: str, parts: List[str]) -> CommandResult:
        from agent import runtime_commands as rc
        return rc.handle_workspaces(self.agent, raw, parts)

    def _handle_keys(self, raw: str, parts: List[str]) -> CommandResult:
        from agent import runtime_commands as rc
        return rc.handle_keys(self.agent, raw, parts)

    def _handle_roles(self, raw: str, parts: List[str]) -> CommandResult:
        from agent import runtime_commands as rc
        return rc.handle_roles(self.agent, raw, parts)

    def _handle_doctor(self, raw: str, parts: List[str]) -> CommandResult:
        from agent import runtime_commands as rc
        return rc.handle_doctor(self.agent, raw, parts)
