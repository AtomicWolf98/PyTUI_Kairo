import json
import os
import re
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


class SearchFileTool(BaseTool):
    name = "search_file"
    description = (
        "Searches for a query string or regex pattern in all files under a directory. "
        "Returns matching files, line numbers, and matching line content."
    )
    permission = Permission.READ
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search term or regex pattern to look for."
            },
            "path": {
                "type": "string",
                "description": "The directory or file path to search. Defaults to current directory ('.')."
            },
            "is_regex": {
                "type": "boolean",
                "description": "Set to true if the query is a regular expression pattern. Default is false."
            }
        },
        "required": ["query"]
    }

    def __init__(self, config=None):
        allow_absolute_outside = False
        max_search_bytes = 1_048_576
        max_search_depth = 10
        max_search_results = 100
        if config is not None:
            allow_absolute_outside = config.policy.get("workspace_path", {}).get("allow_absolute_outside", False)
            limits = config.policy.get("resource_limits", {})
            max_search_bytes = limits.get("max_search_bytes", max_search_bytes)
            max_search_depth = limits.get("max_search_depth", max_search_depth)
            max_search_results = limits.get("max_search_results", max_search_results)
        self.policy = WorkspacePathPolicy(Path.cwd(), allow_absolute_outside=allow_absolute_outside)
        self.max_bytes = max(1024, int(max_search_bytes))
        self.max_depth = max(1, int(max_search_depth))
        self.max_results = max(1, int(max_search_results))

    def classify_scope(self, arguments: str) -> OperationScope:
        args = _parse_tool_args(arguments)
        path = args.get("path") or "."
        return self.policy.scope_for(path)

    def execute(self, query: str, path: str = ".", is_regex: bool = False) -> str:
        try:
            search_path = self.policy.resolve(path)
        except SecurityError as e:
            return f"Error: {e}"

        if not search_path.exists():
            return f"Error: Path '{path}' does not exist."

        results = []
        pattern = None
        if is_regex:
            try:
                pattern = re.compile(query)
            except Exception as e:
                return f"Error compiling regex pattern '{query}': {e}"

        # Determine target files
        files_to_search = []
        if search_path.is_file():
            files_to_search.append(search_path)
        else:
            # Recursively walk directories, skipping virtualenvs or git folders
            for root, dirs, files in os.walk(search_path):
                depth = len(Path(root).relative_to(search_path).parts)
                if depth >= self.max_depth:
                    dirs[:] = []
                    continue
                # Prune in-place to avoid walking down .git or .venv
                dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", "build", "dist")]
                for file in files:
                    files_to_search.append(Path(root) / file)

        # Search within files
        for file_path in files_to_search:
            try:
                size = file_path.stat().st_size
                if size > self.max_bytes:
                    continue
                # Read file as text
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        matched = False
                        if is_regex and pattern:
                            if pattern.search(line):
                                matched = True
                        else:
                            if query in line:
                                matched = True

                        if matched:
                            relative_path = os.path.relpath(file_path, start=os.getcwd())
                            results.append(f"{relative_path}:{line_num}: {line.strip()}")
                            if len(results) >= self.max_results:
                                return "\n".join(results) + f"\n... (more than {self.max_results} matches found, truncated) ..."
            except Exception:
                # Skip unreadable binary files
                pass

        if not results:
            return f"No matches found for '{query}' under '{path}'."

        return "\n".join(results)


class PatchFileTool(BaseTool):
    name = "patch_file"
    description = (
        "Applies a search-and-replace patch to a file. "
        "Crucial: The 'search_block' MUST match exactly a contiguous block of text "
        "in the file, and it must appear EXACTLY ONCE to avoid ambiguity."
    )
    permission = Permission.WRITE
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The path to the file to patch."
            },
            "search_block": {
                "type": "string",
                "description": "The exact block of code/text to find in the file. Include correct indentation and line breaks."
            },
            "replace_block": {
                "type": "string",
                "description": "The block of code/text to replace the search_block with."
            }
        },
        "required": ["path", "search_block", "replace_block"]
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

    def execute(self, path: str, search_block: str, replace_block: str) -> str:
        try:
            file_path = self.policy.resolve(path)
        except SecurityError as e:
            return f"Error: {e}"

        if not file_path.exists():
            return f"Error: File '{path}' does not exist."
        if not file_path.is_file():
            return f"Error: Path '{path}' is a directory, not a file."

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Count occurrences of the search block
            occurrences = content.count(search_block)
            
            if occurrences == 0:
                # Help debug by printing search block details
                return (
                    f"Error: The search_block was not found in '{path}'. "
                    f"Make sure spelling, whitespace, and indentation match exactly."
                )
            elif occurrences > 1:
                return (
                    f"Error: The search_block was found {occurrences} times in '{path}'. "
                    f"Please make the search_block larger/more unique to target only the desired location."
                )

            # Apply replacement
            new_content = content.replace(search_block, replace_block)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                
            return f"Successfully patched file '{path}'. Replaced 1 instance."
        except Exception as e:
            return f"Error patching file '{path}': {str(e)}"
