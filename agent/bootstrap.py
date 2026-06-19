from pathlib import Path

from agent.core import Agent
from tools.base import ToolRegistry
from tools.file_ops import ListDirTool, ReadFileTool, WriteFileTool
from tools.patch_ops import PatchFileTool, SearchFileTool
from tools.shell import PythonExecutor, ShellExecutor
from tools.web import WebFetchTool


def build_registry(config) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ShellExecutor(config=config))
    registry.register(PythonExecutor(config=config))
    registry.register(ReadFileTool(config=config))
    registry.register(WriteFileTool(config=config))
    registry.register(ListDirTool(config=config))
    registry.register(WebFetchTool(config=config))
    registry.register(SearchFileTool(config=config))
    registry.register(PatchFileTool(config=config))
    registry.load_custom_skills(
        config.skills_dir,
        require_hash=config.policy.get("skills", {}).get("require_hash", False),
        workspace_root=Path(config.policy.get("workspace_path", {}).get("root", Path.cwd())).resolve(),
    )
    return registry


def build_agent(config, console=None) -> Agent:
    return Agent(config=config, registry=build_registry(config), console=console)
