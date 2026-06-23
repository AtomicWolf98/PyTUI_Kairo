import json
from pathlib import Path
from typing import Optional

from agent.config import Config
from agent.repl import ShellSession, PythonREPL
from agent.workspace_context import WorkspaceContext
from tools.base import BaseTool
from tools.policy import (
    classify_command_scope,
    classify_python_scope,
    CommandPolicy,
    OperationScope,
    Permission,
    WorkspacePathPolicy,
)


def _parse_tool_args(arguments):
    """Parse tool invocation arguments from a JSON string or dict."""
    try:
        if isinstance(arguments, str):
            return json.loads(arguments) if arguments.strip() else {}
        if isinstance(arguments, dict):
            return arguments
        return {}
    except Exception:
        return {}


class ShellExecutor(BaseTool):
    name = "run_command"
    description = (
        "Executes a command in the persistent system terminal (CMD or PowerShell). "
        "The directory state and environment variables are preserved across calls."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command string to execute in the system terminal."
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set to true to confirm execution of commands that contain shell "
                    "chaining metacharacters (e.g. ; & | || && > >> < $() ` or newlines)."
                ),
                "default": False
            }
        },
        "required": ["command"]
    }
    permission = Permission.EXECUTE

    def __init__(self, config: Config, workspace_context: Optional[WorkspaceContext] = None):
        self.config = config
        command_policy_config = config.policy.get("command", {})
        self.command_policy = CommandPolicy(
            allow_patterns=command_policy_config.get("allow_patterns", []),
            deny_patterns=command_policy_config.get("deny_patterns", []),
            require_confirmation_for_chained=command_policy_config.get(
                "require_confirmation_for_chained", True
            ),
        )
        if workspace_context is not None:
            self.policy = WorkspacePathPolicy(
                workspace_context.root,
                allow_absolute_outside=False,
            )
            self.workspace_context = workspace_context
        else:
            workspace_path_config = config.policy.get("workspace_path", {})
            self.policy = WorkspacePathPolicy(
                Path(config.workspace_root).resolve(),
                allow_absolute_outside=workspace_path_config.get("allow_absolute_outside", False),
            )
            self.workspace_context = None
        # Persistent shell session
        self.session = ShellSession(
            shell_type=config.shell_type,
            cwd=self.policy.root,
        )
        if workspace_context is not None:
            workspace_context.add_listener(self._on_workspace_moved)

    def _on_workspace_moved(self, new_root: Path) -> None:
        """Restart the persistent shell session in the new workspace."""
        self.policy = WorkspacePathPolicy(new_root, allow_absolute_outside=False)
        try:
            self.session.close()
        except Exception:
            pass
        self.session = ShellSession(
            shell_type=self.config.shell_type,
            cwd=new_root,
        )

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        command = args.get("command", "")
        return classify_command_scope(command, self.policy)

    def execute(self, command: str, confirmed: bool = False) -> str:
        """Runs the shell command persistently and returns output."""
        allowed, reason = self.command_policy.classify(command)
        if not allowed:
            if reason == "contains shell chaining metacharacters":
                if not confirmed:
                    return (
                        "Error: This command contains shell chaining metacharacters. "
                        "Re-run with confirmed=true to proceed."
                    )
            else:
                return f"Error: Command not allowed: {reason}"

        self.session.on_output = self.output_callback
        self.emit_output(f"$ {command}\n")
        try:
            output = self.session.execute(command)
            return output
        except Exception as e:
            return f"Failed to execute command: {str(e)}"


class PythonExecutor(BaseTool):
    name = "run_python_code"
    description = (
        "Executes a block of Python code inside a persistent interactive interpreter. "
        "Variables, imports, functions, and state are retained across execution calls. "
        "Use this for complex operations, calculations, or writing scripts."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The block of Python code to execute."
            }
        },
        "required": ["code"]
    }
    permission = Permission.EXECUTE

    def __init__(self, config=None, workspace_context: Optional[WorkspaceContext] = None):
        self.config = config
        policy = None
        if workspace_context is not None:
            workspace_root = workspace_context.root
            allow_absolute_outside = False
        elif config is not None and hasattr(config, "policy"):
            policy = config.policy.get("python")
            workspace_path_config = config.policy.get("workspace_path", {})
            workspace_root = Path(config.workspace_root).resolve()
            allow_absolute_outside = workspace_path_config.get("allow_absolute_outside", False)
        else:
            workspace_root = Path.cwd()
            allow_absolute_outside = False
        self.policy = WorkspacePathPolicy(workspace_root, allow_absolute_outside=allow_absolute_outside)
        # Persistent python REPL
        self.repl = PythonREPL(policy=policy)
        if workspace_context is not None:
            workspace_context.add_listener(self._on_workspace_moved)

    def _on_workspace_moved(self, new_root: Path) -> None:
        """Reset the persistent Python REPL when the workspace moves."""
        self.policy = WorkspacePathPolicy(new_root, allow_absolute_outside=False)
        self.reset_repl()

    def reset_repl(self) -> None:
        """Close and recreate the persistent Python REPL to clear old state."""
        try:
            if hasattr(self.repl, "close"):
                self.repl.close()
        except Exception:
            pass
        policy = None
        if self.config is not None and hasattr(self.config, "policy"):
            policy = self.config.policy.get("python")
        self.repl = PythonREPL(policy=policy)

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        code = args.get("code", "")
        return classify_python_scope(code, self.policy)

    def execute(self, code: str) -> str:
        """Runs python code persistently and returns output."""
        self.emit_output(f">>> {code}\n")
        try:
            output = self.repl.execute(code)
            return output
        except Exception as e:
            return f"Failed to execute Python code: {str(e)}"
