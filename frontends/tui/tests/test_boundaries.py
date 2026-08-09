"""AST + import-surface boundary tests (tui_plan.md gate)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "kairo_tui"

FORBIDDEN_ROOTS = {
    "agent",
    "tools",
    # kairo_kernel private modules (only the public surface is allowed)
    "kairo_kernel.engine",
    "kairo_kernel.services",
    "kairo_kernel.runtime",
    "kairo_kernel.factory",
    "kairo_kernel.kernel",
    "kairo_kernel.mcp",
    "kairo_kernel.memory",
    "kairo_kernel.providers",
    "kairo_kernel.skills",
    "kairo_kernel.storage",
    "kairo_kernel._version",
    "kairo_kernel.config_document",
}

ALLOWED_KERNEL_IMPORTS = {"kairo_kernel", "kairo_kernel.contracts", "kairo_kernel.ports", "kairo_kernel.errors"}


def _import_roots() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((path, alias.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append((path, node.module.split(".")[0]))
    return found


def test_no_forbidden_imports() -> None:
    violations = [
        (str(path), root)
        for path, root in _import_roots()
        if root in FORBIDDEN_ROOTS or any(root.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_ROOTS)
    ]
    assert violations == []


def test_kernel_imports_are_public_surface_only() -> None:
    violations = [
        (str(path), module)
        for path in sorted(PACKAGE.rglob("*.py"))
        for module in _kernel_imports(path)
        if not _is_allowed_kernel_module(module)
    ]
    assert violations == []


def _is_allowed_kernel_module(module: str) -> bool:
    """kairo_kernel, kairo_kernel.contracts.*, kairo_kernel.ports.* and kairo_kernel.errors.* only."""
    if module == "kairo_kernel":
        return True
    return any(
        module == allowed or module.startswith(f"{allowed}.")
        for allowed in ALLOWED_KERNEL_IMPORTS
        if allowed != "kairo_kernel"
    )


def _kernel_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names if alias.name == "kairo_kernel" or alias.name.startswith("kairo_kernel."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kairo_kernel"):
            modules.append(node.module)
    return modules


def test_public_facade_surface_is_available() -> None:
    import kairo_kernel

    expected = {
        "KairoKernel", "__version__", "KernelConfig", "KernelDependencies",
        "KernelError", "KernelResult", "KERNEL_API_VERSION", "build_kernel",
        "contracts", "ports",
    }
    assert expected <= set(dir(kairo_kernel))


def test_compat_screen_deleted() -> None:
    assert not PACKAGE.joinpath("screens/compat.py").exists()


def test_workbench_screen_deleted() -> None:
    """The pre-shell WorkbenchScreen was replaced by the per-page screens."""
    assert not PACKAGE.joinpath("screens/workbench.py").exists()


def test_legacy_agent_ui_deleted() -> None:
    agent_ui = PACKAGE.parents[2] / "agent" / "ui"
    assert not agent_ui.exists()
