from agent.core import Agent
from agent.workspace_context import WorkspaceContext
from tools.base import ToolRegistry
from tools.file_ops import ListDirTool, ReadFileTool, WriteFileTool
from tools.patch_ops import PatchFileTool, SearchFileTool
from tools.shell import PythonExecutor, ShellExecutor
from tools.web import WebFetchTool


def build_registry(config, workspace_context: WorkspaceContext = None) -> ToolRegistry:
    if workspace_context is None:
        workspace_context = WorkspaceContext(
            config.workspace_root,
            allow_absolute_outside=config.policy.get("workspace_path", {}).get("allow_absolute_outside", False),
        )
    registry = ToolRegistry()
    registry.register(ShellExecutor(config=config, workspace_context=workspace_context))
    registry.register(PythonExecutor(config=config, workspace_context=workspace_context))
    registry.register(ReadFileTool(config=config, workspace_context=workspace_context))
    registry.register(WriteFileTool(config=config, workspace_context=workspace_context))
    registry.register(ListDirTool(config=config, workspace_context=workspace_context))
    registry.register(WebFetchTool(config=config))
    registry.register(SearchFileTool(config=config, workspace_context=workspace_context))
    registry.register(PatchFileTool(config=config, workspace_context=workspace_context))
    registry.load_custom_skills(
        config.skills_dir,
        require_hash=config.policy.get("skills", {}).get("require_hash", False),
        workspace_root=workspace_context.root,
    )
    return registry


def build_agent(config, console=None) -> Agent:
    workspace_context = WorkspaceContext(
        config.workspace_root,
        allow_absolute_outside=config.policy.get("workspace_path", {}).get("allow_absolute_outside", False),
    )
    registry = build_registry(config, workspace_context=workspace_context)
    return Agent(config=config, registry=registry, console=console, workspace_context=workspace_context)
