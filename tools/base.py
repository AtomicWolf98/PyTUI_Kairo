import hashlib
import inspect
import json
import importlib.util
import sys
from pathlib import Path
from typing import Dict, Any, Callable, List, Optional, Type

from tools.policy import OperationScope, Permission


class BaseTool:
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    permission: Permission = Permission.READ
    output_callback = None
    # Source tracking: "builtin" or a custom skill file path.
    source: str = "builtin"

    def classify_scope(self, arguments: str) -> OperationScope:
        """Return the risk scope of this invocation. Defaults to internal."""
        return OperationScope.INTERNAL

    def set_output_callback(self, callback):
        self.output_callback = callback

    def emit_output(self, chunk: str):
        if self.output_callback:
            self.output_callback(chunk)

    def execute(self, **kwargs) -> str:
        """Executes the tool logic. Must return a string."""
        raise NotImplementedError("Each tool must implement the execute method.")

    def to_openai_schema(self) -> Dict[str, Any]:
        """Converts tool definition to OpenAI function tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


# Decorator to quickly declare a function as a skill
def skill(name: str = None, description: str = None, permission: Permission = Permission.READ):
    def decorator(func: Callable):
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or f"Execute function {tool_name}"
        
        # Build JSON schema from function signature
        sig = inspect.signature(func)
        properties = {}
        required = []
        
        # Helper to map python types to JSON schema types
        type_mapping = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object"
        }
        
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            
            param_type = param.annotation
            json_type = type_mapping.get(param_type, "string")  # default to string
            
            properties[param_name] = {
                "type": json_type,
                "description": f"Parameter {param_name}"
            }
            
            # If there's no default value, it's required
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
        
        parameters_schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters_schema["required"] = required

        # Create a dynamic BaseTool subclass
        class DynamicTool(BaseTool):
            def __init__(self):
                super().__init__()
                self.name = tool_name
                self.description = tool_desc
                self.parameters = parameters_schema
                self.permission = permission
                self.func = func

            def execute(self, **kwargs) -> str:
                try:
                    res = self.func(**kwargs)
                    return str(res)
                except Exception as e:
                    return f"Error executing {self.name}: {str(e)}"
        
        # Store metadata on function so registry can identify it
        func._is_skill = True
        func._tool_class = DynamicTool
        return func
    return decorator


class ToolRegistry:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        self.output_callback = None

    def register(self, tool: BaseTool):
        if self.output_callback and hasattr(tool, "set_output_callback"):
            tool.set_output_callback(self.output_callback)
        self.tools[tool.name] = tool

    def set_output_callback(self, callback):
        self.output_callback = callback
        for tool in self.tools.values():
            if hasattr(tool, "set_output_callback"):
                tool.set_output_callback(callback)

    def get_schemas(self) -> List[Dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self.tools.values()]

    def execute_tool(self, name: str, arguments: str) -> str:
        if name not in self.tools:
            return f"Error: Tool '{name}' not found in registry."
        
        try:
            # Parse arguments (usually JSON string)
            if isinstance(arguments, str):
                if not arguments.strip():
                    args = {}
                else:
                    args = json.loads(arguments)
            else:
                args = arguments
        except Exception as e:
            return f"Error parsing arguments for tool '{name}': {str(e)}. Raw arguments: {arguments}"

        try:
            tool = self.tools[name]
            return tool.execute(**args)
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _resolve_skills_path(self, skills_dir: str, workspace_root: Optional[Path] = None) -> Path:
        """Resolve skills_dir relative to workspace_root if it is not absolute."""
        skills_path = Path(skills_dir)
        if not skills_path.is_absolute():
            if workspace_root is not None:
                skills_path = Path(workspace_root) / skills_path
            else:
                skills_path = Path.cwd() / skills_path
        return skills_path.resolve()

    def _load_skill_module(self, py_file: Path, require_hash: bool) -> None:
        if require_hash:
            hash_file = py_file.with_suffix(py_file.suffix + ".sha256")
            if not hash_file.exists():
                print(f"[Error] Skill '{py_file.name}' is missing required hash file '{hash_file.name}'.")
                return
            expected = hash_file.read_text(encoding="utf-8").strip().split()[0]
            actual = self._hash_file(py_file)
            if actual != expected:
                print(f"[Error] Skill '{py_file.name}' hash mismatch; refusing to load.")
                return

        # Use a unique module name per skill file to avoid cache collisions.
        module_name = f"kairo_skills_{py_file.stem}_{py_file.stat().st_mtime_ns}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                # Remove any previous module with the same unique name to avoid stale state.
                if module_name in sys.modules:
                    del sys.modules[module_name]
                spec.loader.exec_module(module)

                # Search for subclasses of BaseTool or functions decorated with @skill
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)

                    # 1. Subclass check
                    if (inspect.isclass(attr) and
                        issubclass(attr, BaseTool) and
                        attr is not BaseTool):
                        try:
                            tool_instance = attr()
                            tool_instance.source = str(py_file)
                            self.register(tool_instance)
                        except Exception as ex:
                            print(f"[Error] Failed to instantiate tool {attr_name} from {py_file.name}: {ex}")

                    # 2. Decorated function check
                    elif inspect.isfunction(attr) and getattr(attr, "_is_skill", False):
                        try:
                            tool_class = getattr(attr, "_tool_class")
                            tool_instance = tool_class()
                            tool_instance.source = str(py_file)
                            self.register(tool_instance)
                        except Exception as ex:
                            print(f"[Error] Failed to register skill function {attr_name} from {py_file.name}: {ex}")
        except Exception as e:
            print(f"[Error] Failed to load skill module {py_file.name}: {e}")

    def load_custom_skills(
        self,
        skills_dir: str,
        *,
        require_hash: bool = False,
        workspace_root: Optional[Path] = None,
    ):
        """Loads custom tools/skills from the specified directory dynamically.

        Args:
            skills_dir: Directory containing skill modules. Relative paths are
                resolved against *workspace_root* when provided, otherwise the
                current working directory.
            require_hash: If True, each ``*.py`` file must be accompanied by a
                ``*.py.sha256`` file with a matching SHA-256 digest.
            workspace_root: Optional workspace root used to resolve relative
                skills directories and verify that the directory is inside the
                workspace.
        """
        skills_path = self._resolve_skills_path(skills_dir, workspace_root)
        if workspace_root is not None:
            workspace_root = Path(workspace_root).resolve()
            try:
                skills_path.relative_to(workspace_root)
            except ValueError:
                print(f"[Error] Skills directory '{skills_path}' is outside the workspace '{workspace_root}'.")
                return

        if not skills_path.exists():
            try:
                skills_path.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                print(f"[Error] Failed to create skills directory '{skills_path}': {exc}")
            return

        if not skills_path.is_dir():
            print(f"[Error] Skills path '{skills_path}' is not a directory.")
            return

        # Walk the skills directory and import all .py files
        for py_file in skills_path.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            self._load_skill_module(py_file, require_hash)

    def reload_custom_skills(
        self,
        skills_dir: str,
        *,
        require_hash: bool = False,
        workspace_root: Optional[Path] = None,
    ):
        """Unload old custom skills and load them again from the new workspace.

        This is called after a workspace move so that workspace-specific skills
        are updated without leaking skills from the previous workspace.
        """
        # Remove tools that came from custom skill files.
        custom_tool_names = [
            name for name, tool in self.tools.items()
            if tool.source != "builtin"
        ]
        for name in custom_tool_names:
            del self.tools[name]

        # Clean up cached skill modules to prevent stale code from being reused.
        stale_modules = [
            key for key in list(sys.modules.keys())
            if key.startswith("kairo_skills_") or key.startswith("skills.")
        ]
        for key in stale_modules:
            try:
                del sys.modules[key]
            except Exception:
                pass

        self.load_custom_skills(skills_dir, require_hash=require_hash, workspace_root=workspace_root)
