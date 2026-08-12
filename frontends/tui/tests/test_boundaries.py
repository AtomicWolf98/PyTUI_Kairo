"""X0 boundary gates: single package, no legacy gates, public-surface imports."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1] / "kairo_tui"
FORBIDDEN_KERNEL_IMPORTS = (
    "kairo_kernel.kernel",
    "kairo_kernel.engine",
    "kairo_kernel.services",
    "kairo_kernel.storage",
    "kairo_kernel.runtime",
    "kairo_kernel.tools",
    "kairo_kernel.providers",
    "kairo_kernel.mcp",
    "kairo_kernel.memory",
    "kairo_kernel.skills",
)


def _python_files() -> list[Path]:
    return [path for path in ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_no_setup_screen() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())
    assert "SetupScreen" not in source
    assert "setup_screen" not in source


def test_no_composer_setup_gate() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())
    assert "setup_complete" not in source
    assert "composer.disabled" not in source


def test_single_tui_package() -> None:
    package_dirs = [path.name for path in Path(__file__).parents[1].iterdir() if path.is_dir()]
    assert "kairo_tui" in package_dirs
    assert "kairo_tui_v2" not in package_dirs
    assert "legacy" not in package_dirs
    assert "next" not in package_dirs


def test_frontend_imports_only_kernel_public_surface() -> None:
    violations: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_KERNEL_IMPORTS:
            if re.search(rf"^\s*(from|import)\s+{re.escape(forbidden)}(\.|\s)", text, re.M):
                violations.append(f"{path}: {forbidden}")
    assert violations == []


def test_widgets_do_not_reference_kernel() -> None:
    """Widgets never hold the kernel or its services; contract types are fine."""
    widgets_dir = ROOT / "widgets"
    for path in widgets_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "KairoKernel" not in text, path
        assert "TuiController" not in text, path
        assert "kairo_kernel.kernel" not in text, path
        assert "kairo_kernel.services" not in text, path
        assert "kairo_kernel.engine" not in text, path
        assert "kairo_kernel.storage" not in text, path
        assert "kairo_kernel.runtime" not in text, path


def test_controller_does_not_import_textual_widgets() -> None:
    controller = ROOT / "controller.py"
    text = controller.read_text(encoding="utf-8")
    assert "textual" not in text
    assert "kairo_tui.widgets" not in text
    assert "kairo_tui.dialogs" not in text
    assert "kairo_tui.panels" not in text


def test_no_secret_fields_in_app_state() -> None:
    """AppState has no field that could carry a secret value."""
    import re

    state = ROOT / "state.py"
    text = state.read_text(encoding="utf-8")
    dataclass_block = text.split("class AppState:", 1)[1].split("class ", 1)[0]
    fields = re.findall(r"^    ([a-z_]+):", dataclass_block, re.M)
    for field in fields:
        assert field not in ("secret", "api_key", "token", "password", "api_key_value"), field
