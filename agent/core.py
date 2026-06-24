from pathlib import Path
from typing import Any, Callable, List, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agent.commands import CommandDispatcher, CommandResult
from agent.config import Config
from agent.context_manager import ConversationManager
from agent.interaction import InteractionRunner
from agent import tui_widgets
from agent.session_store import SessionStore
from agent.workspace_context import WorkspaceContext, WorkspaceMoveError
from tools.base import ToolRegistry


class Agent:
    """Facade that exposes configuration, sessions, slash commands and tool shutdown.

    The actual LLM interaction loop lives in :class:`InteractionRunner` to keep
    command handling separate from request/response streaming and tool execution.
    """

    def __init__(self, config: Config, registry: ToolRegistry, console=None, workspace_context: Optional[WorkspaceContext] = None):
        self.config = config
        self.registry = registry
        self.console = console or Console()
        self.workspace_changed: Optional[Callable[[str], None]] = None
        if workspace_context is None:
            workspace_context = WorkspaceContext(
                config.workspace_root,
                allow_absolute_outside=config.policy.get("workspace_path", {}).get("allow_absolute_outside", False),
            )
        self.workspace_context = workspace_context

        self.system_instruction = (
            "You are Kairo, a terminal-native coding assistant. The user interacts with you "
            "in a full-screen terminal UI, and your current working directory is the configured workspace root.\n\n"
            "You have access to tools for file operations (read, write, list, search, patch), "
            "shell commands, Python execution, web fetching, and custom skills from the ./skills directory.\n\n"
            "Authorization level (set by the user, not you):\n"
            "- MANUAL: Every tool call requires user confirmation.\n"
            "- AUTO: Tools that stay inside the workspace run automatically; operations outside the workspace, "
            "system administration, or destructive commands still require confirmation.\n"
            "- YOLO: All tools run automatically without confirmation. Only use if the user explicitly chose this.\n\n"
            "Modes:\n"
            "- Plan Mode: Draft a step-by-step implementation plan first and wait for user approval before acting.\n"
            "- Thinking Mode: Wrap your reasoning in <think>...</think> tags so the UI can display it.\n\n"
            "Guidelines:\n"
            "1. Be concise and precise in terminal output.\n"
            "2. For complex tasks, think step-by-step.\n"
            "3. Call only one tool at a time when tools depend on each other.\n"
            "4. Do not assume paths outside the workspace; if the user asks for external/system changes, describe the impact.\n"
            "5. If a tool fails, report the error and suggest a fix.\n"
            "6. Prefer safe, reversible changes; never run destructive commands silently."
        )

        session_store = None
        if config.sessions.get("enabled", True):
            session_store = SessionStore(
                config.sessions.get("storage_dir", ".kairo/sessions"),
                config.config_path,
            )

        self.conversations = ConversationManager(
            self.system_instruction,
            config.context_window,
            session_store=session_store,
            workspace_root=str(workspace_context.root),
            model_profile=config.active_model_profile,
            authorization_level=config.authorization_level,
        )
        # 0.2.4: wire session config flags from config
        self.conversations._autosave = config.sessions.get("autosave", True)
        self.conversations._max_sessions = int(config.sessions.get("max_sessions", 0) or 0)
        self.conversations._save_interval_seconds = float(
            config.sessions.get("save_interval_seconds", 0) or 0
        )
        self.runner = InteractionRunner(
            config=config,
            registry=registry,
            conversations=self.conversations,
            console=self.console,
            system_instruction=self.system_instruction,
        )

    @property
    def history(self) -> List[Dict[str, Any]]:
        return self.conversations.active.history

    @history.setter
    def history(self, value: List[Dict[str, Any]]):
        self.conversations.active.history = value
        self.conversations.refresh_context()

    @property
    def token_tracker(self):
        return self.conversations.active.token_tracker

    @property
    def active_session_name(self) -> str:
        return self.conversations.active.name

    @property
    def current_task(self) -> str:
        return self.runner.current_task

    @property
    def task_status(self) -> str:
        return self.runner.task_status

    @property
    def llm(self):
        """Backwards-compatible alias for tests and callers."""
        return self.runner.llm

    def compress_context(self, manual: bool = False, tools=None):
        """Backwards-compatible delegate to the interaction runner."""
        return self.runner.compress_context(manual=manual, tools=tools)

    def ensure_context_capacity(self, tools=None, emergency: bool = False) -> bool:
        """Backwards-compatible delegate to the interaction runner."""
        return self.runner.ensure_context_capacity(tools=tools, emergency=emergency)

    def reset_history(self):
        """Resets the chat history to only the system instruction."""
        self.conversations.clear_active()

    def move_workspace(self, target: str | Path) -> CommandResult:
        """Move the workspace root to *target* and update all dependent state.

        This is the single transaction entry point used by both plain and TUI modes.
        """
        target_path = Path(target).expanduser().resolve()
        try:
            self.workspace_context.move(target_path)
        except WorkspaceMoveError as exc:
            return CommandResult(
                handled=True,
                success=False,
                message=f"Workspace move failed: {exc}",
                data={"kind": "workspace_moved", "root": str(target_path)},
            )
        except Exception as exc:
            return CommandResult(
                handled=True,
                success=False,
                message=f"Workspace move failed: {exc}",
                data={"kind": "workspace_moved", "root": str(target_path)},
            )

        new_root = str(target_path)
        self.config.workspace_root = new_root
        self.config.save()

        self.conversations.update_runtime_state(
            workspace_root=new_root,
            model_profile=self.config.active_model_profile,
            authorization_level=self.config.authorization_level,
        )
        self.conversations.save_active(reason="workspace_move")

        # Reset Python REPL so old variables and cwd semantics do not leak.
        python_executor = self.registry.tools.get("run_python_code")
        if python_executor is not None and hasattr(python_executor, "reset_repl"):
            try:
                python_executor.reset_repl()
                self.console.print("[dim]Python REPL reset after workspace move.[/dim]")
            except Exception as exc:
                self.console.print(f"[yellow]Python REPL reset failed: {exc}[/yellow]")

        # Reload custom skills from the new workspace.
        if hasattr(self.registry, "reload_custom_skills"):
            try:
                self.registry.reload_custom_skills(
                    self.config.skills_dir,
                    require_hash=self.config.policy.get("skills", {}).get("require_hash", False),
                    workspace_root=target_path,
                )
            except Exception as exc:
                self.console.print(f"[yellow]Custom skills reload failed: {exc}[/yellow]")

        if self.workspace_changed:
            self.workspace_changed(new_root)

        notice = f"Workspace moved to: {target_path}"
        # 0.2.4: workspace notice is delivered via CommandResult.message / UI event,
        # NOT appended as a system message to history (violates system-prefix invariant).
        return CommandResult(
            handled=True,
            success=True,
            message=notice,
            refresh_ui=True,
            data={"kind": "workspace_moved", "root": new_root},
        )

    def shutdown(self):
        """Release persistent resources held by registered tools and save sessions."""
        self.conversations.save_all(reason="shutdown")
        for tool in self.registry.tools.values():
            if hasattr(tool, "session") and hasattr(tool.session, "close"):
                try:
                    tool.session.close()
                except Exception:
                    pass
            if hasattr(tool, "repl") and hasattr(tool.repl, "close"):
                try:
                    tool.repl.close()
                except Exception:
                    pass

    def print_welcome(self):
        """Displays the welcome message on CLI startup."""
        welcome_text = Text()
        welcome_text.append("=== KAIRO ===\n", style="bold cyan")
        welcome_text.append("Kai is awake and ready.\n\n", style="italic gray")
        welcome_text.append(f"Model: {self.config.model}\n", style="cyan")
        welcome_text.append(f"Active Target: {self.config.active_model_profile}\n", style="cyan")
        welcome_text.append(f"Base URL: {self.config.base_url}\n", style="cyan")
        welcome_text.append(f"Skills loaded: {list(self.registry.tools.keys())}\n\n", style="green")
        welcome_text.append("Toggles: ", style="bold")
        welcome_text.append(f"Plan Mode: {'ON' if self.config.plan_mode else 'OFF'} | ", style="magenta" if self.config.plan_mode else "gray")
        welcome_text.append(f"Auto Mode: {'ON' if self.config.auto_mode else 'OFF'} | ", style="red" if self.config.auto_mode else "gray")
        welcome_text.append(f"Thinking Mode: {'ON' if self.config.thinking_mode else 'OFF'}\n", style="yellow" if self.config.thinking_mode else "gray")
        welcome_text.append("Type /help to see available commands.", style="dim")

        self.console.print(Panel(welcome_text, border_style="cyan", title="Kairo", subtitle="v0.2.4"))

    def handle_command(self, user_input: str) -> bool:
        """
        Handles slash commands entered by the user.
        Returns True if a command was matched and handled, False otherwise.
        """
        dispatcher = CommandDispatcher(self)
        result = dispatcher.dispatch(user_input)
        if not result.handled:
            return False

        if result.exit_app:
            self.console.print("[bold red]Kairo is shutting down. Goodbye![/bold red]")
            self.shutdown()
            exit(0)

        if result.message:
            style = "bold green" if result.success else "bold yellow"
            self.console.print(f"[{style}]{result.message}[/{style}]")

        if result.interactive:
            kind = result.data.get("kind")
            if kind == "sessions":
                options = result.data["options"]
                default_index = result.data["default_index"]
                idx = tui_widgets.select_menu("Switch Conversation:", options, default_index=default_index)
                if isinstance(idx, int) and 0 <= idx < len(self.conversations.sessions):
                    session = self.conversations.sessions[idx]
                    self.conversations.switch_session(session.id)
                    self.console.print(f"[bold green]Switched to conversation:[/bold green] {session.name}")
                else:
                    self.console.print("[bold yellow]Session switch cancelled: invalid selection.[/bold yellow]")
            elif kind == "model":
                profiles = result.data["profiles"]
                default_index = result.data["default_index"]
                idx = tui_widgets.select_menu("Select provider / model:", profiles, default_index=default_index)
                if isinstance(idx, int) and 0 <= idx < len(profiles):
                    selected_profile = profiles[idx]
                    self.config.apply_model_profile(selected_profile)
                    self.conversations.set_context_window(self.config.context_window)
                    self.conversations.update_runtime_state(model_profile=self.config.active_model_profile)
                    self.conversations.save_all(reason="model_switch")
                    self.config.save()
                    self.console.print(
                        f"Active target changed to [bold cyan]{selected_profile}[/bold cyan] "
                        f"([bold]{self.config.model}[/bold]) and config saved."
                    )
                else:
                    self.console.print("[bold yellow]Model switch cancelled: invalid selection.[/bold yellow]")

        return True

    def run_interaction(self, user_input: str) -> None:
        """Executes the agent logic for a single user interaction in the local console."""
        if user_input.strip().startswith("/"):
            if self.handle_command(user_input):
                return
        self.runner.run_interaction(user_input)

    def run_interaction_events(
        self,
        user_input: str,
        emit: Callable[[str, Any], None],
        approve: Optional[Callable[[str, List[str], int], int]] = None,
        request_text: Optional[Callable[[str], str]] = None,
    ) -> None:
        """Run one interaction without terminal rendering, emitting structured UI events."""
        self.runner.run_interaction_events(user_input, emit, approve=approve, request_text=request_text)
