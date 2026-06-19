import json
import os
from pathlib import Path
from tools.base import BaseTool
from tools.policy import OperationScope, Permission, SecurityError, WorkspacePathPolicy


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


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Reads and returns the contents of a file from the local file system."
    permission = Permission.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to read (absolute or relative to current directory)."
            }
        },
        "required": ["path"]
    }

    def __init__(self, config=None):
        allow_absolute_outside = False
        max_read_bytes = 1_048_576
        if config is not None:
            allow_absolute_outside = config.policy.get("workspace_path", {}).get("allow_absolute_outside", False)
            max_read_bytes = config.policy.get("resource_limits", {}).get("max_read_bytes", max_read_bytes)
        self.policy = WorkspacePathPolicy(Path.cwd(), allow_absolute_outside=allow_absolute_outside)
        self.max_bytes = max(1024, int(max_read_bytes))

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        path = args.get("path") or "."
        return self.policy.scope_for(path)

    def execute(self, path: str) -> str:
        try:
            file_path = self.policy.resolve(path)
            if not file_path.exists():
                return f"Error: File '{path}' does not exist."
            if not file_path.is_file():
                return f"Error: Path '{path}' is a directory, not a file."

            size = file_path.stat().st_size
            if size > self.max_bytes:
                return (
                    f"Error: File '{path}' is {size:,} bytes, exceeding the "
                    f"configured read limit of {self.max_bytes:,} bytes. "
                    f"Use a more targeted tool or increase resource_limits.max_read_bytes."
                )

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(self.max_bytes + 1)
            if len(content.encode("utf-8", errors="replace")) > self.max_bytes:
                content = content[: self.max_bytes]
            return content
        except SecurityError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file '{path}': {str(e)}"


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Writes content to a file on the local file system. If the file exists, it will be overwritten."
    permission = Permission.WRITE
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to write (absolute or relative to current directory)."
            },
            "content": {
                "type": "string",
                "description": "The exact text content to write into the file."
            }
        },
        "required": ["path", "content"]
    }

    def __init__(self, config=None):
        allow_absolute_outside = False
        if config is not None:
            allow_absolute_outside = config.policy.get("workspace_path", {}).get("allow_absolute_outside", False)
        self.policy = WorkspacePathPolicy(Path.cwd(), allow_absolute_outside=allow_absolute_outside)

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        path = args.get("path") or "."
        return self.policy.scope_for(path)

    def execute(self, path: str, content: str) -> str:
        try:
            file_path = self.policy.resolve(path)
            # Create parent directories if they don't exist
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} characters to '{path}'."
        except SecurityError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing to file '{path}': {str(e)}"


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "Lists files and subdirectories in a folder on the local file system."
    permission = Permission.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The directory path to list. Defaults to the current directory ('.')."
            }
        }
    }

    def __init__(self, config=None):
        allow_absolute_outside = False
        if config is not None:
            allow_absolute_outside = config.policy.get("workspace_path", {}).get("allow_absolute_outside", False)
        self.policy = WorkspacePathPolicy(Path.cwd(), allow_absolute_outside=allow_absolute_outside)

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        path = args.get("path") or "."
        return self.policy.scope_for(path)

    def execute(self, path: str = ".") -> str:
        try:
            dir_path = self.policy.resolve(path)
            if not dir_path.exists():
                return f"Error: Directory '{path}' does not exist."
            if not dir_path.is_dir():
                return f"Error: Path '{path}' is a file, not a directory."
            
            items = []
            for item in dir_path.iterdir():
                item_type = "DIR" if item.is_dir() else "FILE"
                size_info = f" ({item.stat().st_size} bytes)" if item.is_file() else ""
                items.append(f"[{item_type}] {item.name}{size_info}")
            
            if not items:
                return f"Directory '{path}' is empty."
            
            return "\n".join(items)
        except SecurityError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory '{path}': {str(e)}"
