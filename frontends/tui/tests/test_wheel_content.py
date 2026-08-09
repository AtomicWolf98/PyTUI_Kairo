"""TUI wheel payload + isolated-install smoke (mirrors tests/kernel/packaging/test_alpha_wheel.py)."""
from __future__ import annotations

import os
import subprocess
import venv
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = "kairo_tui-0.4.0a2-py3-none-any.whl"


def wheel_path() -> Path:
    configured = os.environ.get("KAIRO_TUI_WHEEL", "").strip()
    wheel = Path(configured) if configured else ROOT / "dist" / EXPECTED
    if not wheel.is_absolute():
        wheel = ROOT / wheel
    if not wheel.is_file():
        pytest.fail(f"Build the TUI alpha wheel before running wheel smoke tests: {wheel}")
    return wheel.resolve()


def kernel_wheel_path() -> Path:
    configured = os.environ.get("KAIRO_KERNEL_WHEEL", "").strip()
    wheel = Path(configured) if configured else ROOT / "dist" / "kairo_kernel-0.4.0a2-py3-none-any.whl"
    if not wheel.is_absolute():
        wheel = ROOT / wheel
    if not wheel.is_file():
        pytest.fail(f"Build the kernel alpha wheel before running wheel smoke tests: {wheel}")
    return wheel.resolve()


def test_tui_wheel_contains_only_tui_and_distribution_metadata() -> None:
    with zipfile.ZipFile(wheel_path()) as archive:
        names = archive.namelist()
    assert "kairo_tui/py.typed" in names
    assert "kairo_tui/_version.py" in names
    assert all(name.startswith(("kairo_tui/", "kairo_tui-0.4.0a2.dist-info/")) for name in names)
    assert not any(name.startswith(("agent/", "tools/", "tests/", "web/")) for name in names)
    assert not any(name.startswith("kairo.py") for name in names)
    assert any(name.endswith("entry_points.txt") for name in names)  # kairo-tui console script


def test_tui_wheel_installs_isolated_and_smokes() -> None:
    tmp = __import__("tempfile").TemporaryDirectory()
    venv_dir = Path(tmp.name) / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall",
         str(kernel_wheel_path()), str(wheel_path())],
        check=True, capture_output=True, text=True,
    )
    working = Path(tmp.name) / "outside-source"
    working.mkdir()
    child = dict(os.environ)
    child.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(python), "-m", "kairo_tui", "--headless-smoke"],
        cwd=working, env=child, check=True, capture_output=True, text=True,
    )
    assert "KAIRO_TUI_SMOKE_OK" in completed.stdout
