import hashlib
import inspect
import json
import sys
import types
from pathlib import Path
from typing import Dict, Any, Callable, List, Optional

from tools.policy import OperationScope, Permission
from tools.skill_trust import (
    SkillCandidate,
    SkillTrustError,
    SkillTrustStore,
    directory_manifest_snapshot,
)


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
    def __init__(self, skill_trust_store: Optional[SkillTrustStore] = None):
        self.tools: Dict[str, BaseTool] = {}
        self.output_callback = None
        self.skill_trust_store = skill_trust_store or SkillTrustStore()
        self.custom_skill_candidates: List[SkillCandidate] = []
        self.custom_skill_warnings: List[str] = []
        self._custom_skill_context: Optional[Dict[str, Any]] = None

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
        self.refresh_custom_skill_trust()
        return [tool.to_openai_schema() for tool in self.tools.values()]

    def execute_tool(self, name: str, arguments: str) -> str:
        if name in self.tools and self.tools[name].source != "builtin":
            self.refresh_custom_skill_trust()
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

    def _load_skill_module(
        self,
        py_file: Path,
        require_hash: bool,
        *,
        expected_manifest_digest: str,
        skills_root: Path,
    ) -> None:
        try:
            manifest_digest, manifest_files = directory_manifest_snapshot(skills_root)
        except SkillTrustError as exc:
            print(f"[Error] Failed to verify skill manifest: {exc}")
            return
        if manifest_digest != expected_manifest_digest:
            print(f"[Error] Skill manifest changed before loading '{py_file.name}'; refusing to load.")
            return
        relative_path = py_file.relative_to(skills_root).as_posix()
        source_bytes = manifest_files.get(relative_path)
        if source_bytes is None:
            print(f"[Error] Skill '{py_file.name}' disappeared from the verified manifest.")
            return

        if require_hash:
            hash_file = py_file.with_suffix(py_file.suffix + ".sha256")
            hash_relative = hash_file.relative_to(skills_root).as_posix()
            hash_bytes = manifest_files.get(hash_relative)
            if hash_bytes is None:
                print(f"[Error] Skill '{py_file.name}' is missing required hash file '{hash_file.name}'.")
                return
            expected = hash_bytes.decode("utf-8").strip().split()[0]
            actual = hashlib.sha256(source_bytes).hexdigest()
            if actual != expected:
                print(f"[Error] Skill '{py_file.name}' hash mismatch; refusing to load.")
                return

        # Compile exactly the bytes that participated in the trusted manifest.
        module_name = f"kairo_skills_{py_file.stem}_{expected_manifest_digest[:16]}"
        try:
            module = types.ModuleType(module_name)
            module.__file__ = str(py_file)
            module.__package__ = ""
            sys.modules.pop(module_name, None)
            sys.modules[module_name] = module
            code = compile(source_bytes, str(py_file), "exec")
            exec(code, module.__dict__)

            # Build instances but delay registry mutation until the full
            # directory manifest is checked again.
            pending_tools: List[BaseTool] = []
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                ):
                    try:
                        tool_instance = attr()
                        tool_instance.source = str(py_file)
                        pending_tools.append(tool_instance)
                    except Exception as ex:
                        print(f"[Error] Failed to instantiate tool {attr_name} from {py_file.name}: {ex}")
                elif inspect.isfunction(attr) and getattr(attr, "_is_skill", False):
                    try:
                        tool_class = getattr(attr, "_tool_class")
                        tool_instance = tool_class()
                        tool_instance.source = str(py_file)
                        pending_tools.append(tool_instance)
                    except Exception as ex:
                        print(f"[Error] Failed to register skill function {attr_name} from {py_file.name}: {ex}")

            after_digest, _ = directory_manifest_snapshot(skills_root)
            if after_digest != expected_manifest_digest:
                sys.modules.pop(module_name, None)
                print(f"[Error] Skill manifest changed while loading '{py_file.name}'; refusing to register.")
                return
            for tool_instance in pending_tools:
                self._register_custom_tool(tool_instance)
        except Exception as e:
            sys.modules.pop(module_name, None)
            print(f"[Error] Failed to load skill module {py_file.name}: {e}")

    def _register_custom_tool(self, tool: BaseTool) -> None:
        existing = self.tools.get(tool.name)
        if existing is not None and existing.source == "builtin":
            print(f"[Error] Custom skill cannot replace built-in tool '{tool.name}'.")
            return
        self.register(tool)

    def load_custom_skills(
        self,
        skills_dir: str,
        *,
        require_hash: bool = False,
        workspace_root: Optional[Path] = None,
    ):
        """Discover custom skills and import only explicitly trusted files.

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
        if workspace_root is None:
            skills_path = self._resolve_skills_path(skills_dir)
            workspace_root = skills_path.parent
        workspace_root = Path(workspace_root).resolve()
        self._custom_skill_context = {
            "skills_dir": skills_dir,
            "require_hash": bool(require_hash),
            "workspace_root": workspace_root,
        }
        self.custom_skill_warnings = []
        try:
            candidates = self.skill_trust_store.discover(workspace_root, skills_dir)
        except SkillTrustError as exc:
            self.custom_skill_candidates = []
            self.custom_skill_warnings.append(str(exc))
            print(f"[Error] {exc}")
            return

        self.custom_skill_candidates = candidates
        for candidate in candidates:
            if not candidate.trusted:
                continue
            py_file = Path(candidate.skills_root) / candidate.relative_path
            self._load_skill_module(
                py_file,
                require_hash,
                expected_manifest_digest=candidate.digest,
                skills_root=Path(candidate.skills_root),
            )

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

    def list_custom_skills(self) -> List[Dict[str, Any]]:
        """Return trusted and pending custom skills without executing pending code."""
        self.refresh_custom_skill_trust()
        return [candidate.to_dict() for candidate in self.custom_skill_candidates]

    def trust_custom_skill(self, relative_path: str, expected_digest: str) -> Dict[str, Any]:
        """Trust the reviewed manifest and load the selected skill."""
        if self._custom_skill_context is None:
            raise SkillTrustError("Custom skills have not been configured.")
        context = self._custom_skill_context
        candidate = self.skill_trust_store.trust(
            context["workspace_root"],
            context["skills_dir"],
            relative_path,
            expected_digest,
        )
        self.reload_custom_skills(**context)
        return candidate.to_dict()

    def revoke_custom_skill(self, relative_path: str) -> bool:
        """Revoke a workspace skill and unload custom tools immediately."""
        if self._custom_skill_context is None:
            raise SkillTrustError("Custom skills have not been configured.")
        context = self._custom_skill_context
        revoked = self.skill_trust_store.revoke(
            context["workspace_root"],
            context["skills_dir"],
            relative_path,
        )
        self.reload_custom_skills(**context)
        return revoked

    def trust_all(self, expected_digest: str) -> List[Dict[str, Any]]:
        """Atomically trust and load the complete reviewed skills manifest."""
        if self._custom_skill_context is None:
            raise SkillTrustError("Custom skills have not been configured.")
        context = self._custom_skill_context
        candidates = self.skill_trust_store.trust_all(
            context["workspace_root"],
            context["skills_dir"],
            expected_digest,
        )
        self.reload_custom_skills(**context)
        return [candidate.to_dict() for candidate in candidates]

    def revoke_all(self) -> bool:
        """Atomically revoke and unload all custom skills for the workspace."""
        if self._custom_skill_context is None:
            raise SkillTrustError("Custom skills have not been configured.")
        context = self._custom_skill_context
        revoked = self.skill_trust_store.revoke_all(
            context["workspace_root"],
            context["skills_dir"],
        )
        self.reload_custom_skills(**context)
        return revoked

    def refresh_custom_skill_trust(self) -> bool:
        """Unload skills whose directory manifest no longer matches trust."""
        if self._custom_skill_context is None:
            return False
        context = self._custom_skill_context
        try:
            current = self.skill_trust_store.discover(
                context["workspace_root"],
                context["skills_dir"],
            )
        except SkillTrustError as exc:
            current = []
            self.custom_skill_warnings = [str(exc)]

        previous_state = {
            (item.relative_path, item.digest, item.trusted)
            for item in self.custom_skill_candidates
        }
        current_state = {
            (item.relative_path, item.digest, item.trusted)
            for item in current
        }
        if current_state == previous_state:
            return False
        self.reload_custom_skills(**context)
        return True
