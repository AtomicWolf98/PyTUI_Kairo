from pathlib import Path
from typing import Any, Callable, List, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text

from agent.commands import build_help_markdown
from agent.config import Config
from agent.context_manager import ConversationManager
from agent.interaction import InteractionRunner
from agent import tui_widgets
from tools.base import ToolRegistry


class Agent:
    """Facade that exposes configuration, sessions, slash commands and tool shutdown.

    The actual LLM interaction loop lives in :class:`InteractionRunner` to keep
    command handling separate from request/response streaming and tool execution.
    """

    def __init__(self, config: Config, registry: ToolRegistry, console=None):
        self.config = config
        self.registry = registry
        self.console = console or Console()
        self.workspace_changed: Optional[Callable[[str], None]] = None

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
        self.conversations = ConversationManager(self.system_instruction, config.context_window)
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

    def _update_tool_workspace_roots(self, root: Path):
        """Update WorkspacePathPolicy instances in all registered tools."""
        from tools.policy import WorkspacePathPolicy
        for tool in self.registry.tools.values():
            policy = getattr(tool, "policy", None)
            if isinstance(policy, WorkspacePathPolicy):
                allow = policy.allow_absolute_outside
                tool.policy = WorkspacePathPolicy(root, allow_absolute_outside=allow)

    def shutdown(self):
        """Release persistent resources held by registered tools."""
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

        self.console.print(Panel(welcome_text, border_style="cyan", title="Kairo", subtitle="v0.2.0"))

    def handle_command(self, user_input: str) -> bool:
        """
        Handles slash commands entered by the user.
        Returns True if a command was matched and handled, False otherwise.
        """
        parts = user_input.strip().split()
        cmd = parts[0].lower()

        if cmd == "/exit" or cmd == "/quit":
            self.console.print("[bold red]Kairo is shutting down. Goodbye![/bold red]")
            self.shutdown()
            exit(0)

        elif cmd == "/help":
            self.console.print(Panel(Markdown(build_help_markdown()), title="Commands Help", border_style="cyan"))
            return True

        elif cmd == "/plan":
            self.config.plan_mode = not self.config.plan_mode
            self.config.save()
            status = "[bold green]ON[/bold green]" if self.config.plan_mode else "[bold red]OFF[/bold red]"
            self.console.print(f"Plan Mode is now {status} and config saved.")
            return True

        elif cmd == "/manual":
            self.config.authorization_level = "manual"
            self.config.save()
            self.console.print("[bold green]Authorization level set to MANUAL. Every tool will ask for confirmation.[/bold green]")
            return True

        elif cmd == "/auto":
            self.config.authorization_level = "auto"
            self.config.save()
            self.console.print("[bold green]Authorization level set to AUTO. Workspace-internal tools run automatically; external/system/destructive actions still require confirmation.[/bold green]")
            return True

        elif cmd == "/yolo":
            self.config.authorization_level = "yolo"
            self.config.save()
            self.console.print("[bold red]Authorization level set to YOLO. All tools will execute automatically without confirmation. Use with extreme care.[/bold red]")
            return True

        elif cmd == "/think":
            self.config.thinking_mode = not self.config.thinking_mode
            self.config.save()
            status = "[bold green]ON[/bold green]" if self.config.thinking_mode else "[bold red]OFF[/bold red]"
            self.console.print(f"Thinking Mode is now {status} and config saved.")
            return True

        elif cmd == "/skills":
            skills_text = ""
            for name, tool in self.registry.tools.items():
                skills_text += f"- **{name}**: {tool.description}\n"
            self.console.print(Panel(Markdown(skills_text or "No skills loaded."), title="Skills List", border_style="green"))
            return True

        elif cmd == "/clear":
            self.reset_history()
            self.console.print("[bold green]Conversation history cleared.[/bold green]")
            return True

        elif cmd == "/new":
            name = user_input.strip()[len(parts[0]):].strip()
            session = self.conversations.create_session(name or None)
            self.console.print(f"[bold green]Created conversation:[/bold green] {session.name}")
            return True

        elif cmd == "/sessions":
            options = self.conversations.session_menu_options()
            current_idx = next(
                (index for index, session in enumerate(self.conversations.sessions)
                 if session.id == self.conversations.active_session_id),
                0,
            )
            idx = tui_widgets.select_menu("Switch Conversation:", options, default_index=current_idx)
            if not isinstance(idx, int) or not (0 <= idx < len(self.conversations.sessions)):
                self.console.print("[bold yellow]Session switch cancelled: invalid selection.[/bold yellow]")
                return True
            session = self.conversations.sessions[idx]
            self.conversations.switch_session(session.id)
            self.console.print(f"[bold green]Switched to conversation:[/bold green] {session.name}")
            return True

        elif cmd == "/compress":
            success, message = self.runner.compress_context(manual=True)
            style = "bold green" if success else "bold yellow"
            self.console.print(f"[{style}]{message}[/{style}]")
            return True

        elif cmd == "/config":
            cfg_text = (
                f"Model: {self.config.model}\n"
                f"Active Provider: {self.config.active_provider}\n"
                f"Active Model: {self.config.active_model}\n"
                f"Active Target: {self.config.active_model_profile}\n"
                f"Base URL: {self.config.base_url}\n"
                f"Temperature: {self.config.temperature}\n"
                f"Max Tokens: {self.config.max_tokens}\n"
                f"Context Window: {self.config.context_window}\n"
                f"Context Management: {self.config.context_management}\n"
                f"Active Conversation: {self.active_session_name}\n"
                f"Configured Targets: {', '.join(self.config.get_model_profile_names())}\n"
                f"Auto Mode: {'ON' if self.config.auto_mode else 'OFF'}\n"
                f"Plan Mode: {'ON' if self.config.plan_mode else 'OFF'}\n"
                f"Thinking Mode: {'ON' if self.config.thinking_mode else 'OFF'}\n"
                f"Skills Directory: {self.config.skills_dir}\n"
            )
            self.console.print(Panel(cfg_text, title="Configuration Settings", border_style="cyan"))
            return True

        elif cmd == "/model":
            profiles = self.config.get_model_profile_names()
            if not profiles:
                self.console.print("[bold yellow]No model profiles configured.[/bold yellow]")
                return True

            default_idx = 0
            if self.config.active_model_profile in profiles:
                default_idx = profiles.index(self.config.active_model_profile)

            idx = tui_widgets.select_menu("Select provider / model:", profiles, default_index=default_idx)
            if not isinstance(idx, int) or not (0 <= idx < len(profiles)):
                self.console.print("[bold yellow]Model switch cancelled: invalid selection.[/bold yellow]")
                return True
            selected_profile = profiles[idx]
            self.config.apply_model_profile(selected_profile)
            self.conversations.set_context_window(self.config.context_window)
            self.config.save()
            self.console.print(
                f"Active target changed to [bold cyan]{selected_profile}[/bold cyan] "
                f"([bold]{self.config.model}[/bold]) and config saved."
            )
            return True

        elif cmd == "/undo":
            user_idx = -1
            for i in range(len(self.history) - 1, -1, -1):
                if self.history[i]["role"] == "user":
                    user_idx = i
                    break

            if user_idx != -1:
                undone_user_msg = self.history[user_idx]["content"]
                self.history = self.history[:user_idx]
                self.console.print(f"[bold green]Undid last conversation turn.[/bold green] Removed user message: [italic]\"{undone_user_msg}\"[/italic]")
            else:
                self.console.print("[bold yellow]No conversation turn to undo.[/bold yellow]")
            return True

        elif cmd == "/workspace":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "move" and len(parts) > 2:
                target = user_input.strip()[len("/workspace move"):].strip()
                target_path = Path(target).expanduser().resolve()
                if not target_path.exists():
                    self.console.print(f"[bold red]Workspace move failed: path does not exist: {target_path}[/bold red]")
                    return True
                if not target_path.is_dir():
                    self.console.print(f"[bold red]Workspace move failed: path is not a directory: {target_path}[/bold red]")
                    return True
                try:
                    test_file = target_path / ".kairo_write_test"
                    test_file.touch(exist_ok=True)
                    test_file.unlink(missing_ok=True)
                except Exception as exc:
                    self.console.print(f"[bold red]Workspace move failed: directory is not writable: {target_path} ({exc})[/bold red]")
                    return True

                self.config.workspace_root = str(target_path)
                self.config.save()
                self._update_tool_workspace_roots(target_path)
                if self.workspace_changed:
                    self.workspace_changed(str(target_path))
                self.console.print(f"[bold green]Workspace moved to:[/bold green] {target_path}")
                return True

            self.console.print(f"[bold cyan]Current workspace:[/bold cyan] {Path(self.config.workspace_root).resolve()}")
            self.console.print("Use '/workspace move <path>' to switch to another directory.")
            return True

        return False

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
