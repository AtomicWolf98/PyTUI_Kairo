from __future__ import annotations

import ast
from pathlib import Path

import kairo_kernel

BANNED_ROOTS = {
    "agent",
    "fastapi",
    "rich",
    "textual",
    "tools",
    "uvicorn",
}


def test_kernel_has_no_legacy_ui_or_framework_imports() -> None:
    root = Path(kairo_kernel.__file__).resolve().parent
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            line = 0
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
                line = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = (node.module,)
                line = node.lineno
            for module in modules:
                if module.split(".", 1)[0] in BANNED_ROOTS:
                    relative = path.relative_to(root).as_posix()
                    violations.append(f"{relative}:{line}: {module}")
    assert violations == []


def test_composition_root_is_the_only_root_runtime_entrypoint() -> None:
    public = set(kairo_kernel.__all__)
    assert {"KairoKernel", "KernelConfig", "KernelDependencies", "build_kernel"} <= public
    assert not ({"TurnEngine", "EventBus", "SQLiteDatabase", "SessionService"} & public)
