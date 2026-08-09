from __future__ import annotations

import email
import os
import subprocess
import sys
import tomllib
import venv
import zipfile
from pathlib import Path

import pytest

from kairo_kernel._version import __version__

ROOT = Path(__file__).parents[3]
EXPECTED_WHEEL = "kairo_kernel-0.4.0a2-py3-none-any.whl"


def wheel_path() -> Path:
    configured = os.environ.get("KAIRO_WHEEL", "").strip()
    wheel = Path(configured) if configured else ROOT / "dist" / EXPECTED_WHEEL
    if not wheel.is_absolute():
        wheel = ROOT / wheel
    if not wheel.is_file():
        pytest.fail(f"Build the alpha wheel before running packaging smoke tests: {wheel}")
    return wheel.resolve()


def test_project_metadata_defines_kernel_only_alpha_distribution() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = project["project"]
    setuptools = project["tool"]["setuptools"]

    assert metadata["name"] == "kairo-kernel"
    assert metadata["requires-python"] == ">=3.11"
    assert "scripts" not in metadata
    assert metadata["dependencies"] == ["aiosqlite>=0.22.1,<1", "httpx>=0.27,<1"]
    assert set(metadata["optional-dependencies"]) == {"openai", "anthropic", "mcp", "otel", "all", "dev"}
    assert setuptools["packages"]["find"]["include"] == ["kairo_kernel", "kairo_kernel.*"]
    assert setuptools["dynamic"]["version"]["attr"] == "kairo_kernel._version.__version__"
    assert __version__ == "0.4.0a2"


def test_wheel_contains_only_kernel_and_distribution_metadata() -> None:
    wheel = wheel_path()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert "kairo_kernel/py.typed" in names
    assert "kairo_kernel/_version.py" in names
    assert all(name.startswith(("kairo_kernel/", "kairo_kernel-0.4.0a2.dist-info/")) for name in names)
    assert not any(name.endswith("entry_points.txt") for name in names)
    assert not any(name.startswith(("agent/", "tools/", "tests/", "web/")) for name in names)
    assert "kairo.py" not in names
    assert "main.py" not in names


def test_wheel_metadata_has_version_python_dependencies_and_extras() -> None:
    with zipfile.ZipFile(wheel_path()) as archive:
        raw = archive.read("kairo_kernel-0.4.0a2.dist-info/METADATA").decode("utf-8")
    metadata = email.message_from_string(raw)
    requirements = metadata.get_all("Requires-Dist", [])

    assert metadata["Name"] == "kairo-kernel"
    assert metadata["Version"] == "0.4.0a2"
    assert metadata["Requires-Python"] == ">=3.11"
    assert any(requirement.startswith("aiosqlite") and "extra" not in requirement for requirement in requirements)
    assert any(requirement.startswith("httpx") and "extra" not in requirement for requirement in requirements)
    assert set(metadata.get_all("Provides-Extra", [])) == {"all", "anthropic", "dev", "mcp", "openai", "otel"}


def test_wheel_installs_and_imports_outside_source_tree(tmp_path: Path) -> None:
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel_path())],
        check=True,
        capture_output=True,
        text=True,
    )
    working = tmp_path / "outside-source"
    working.mkdir()
    child_environment = dict(os.environ)
    child_environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import pathlib; import kairo_kernel; from kairo_kernel._version import __version__; "
                "import importlib.metadata; print(__version__); print(importlib.metadata.version('kairo-kernel')); "
                "print(pathlib.Path(kairo_kernel.__file__).resolve())"
            ),
        ],
        cwd=working,
        env=child_environment,
        check=True,
        capture_output=True,
        text=True,
    )

    lines = completed.stdout.strip().splitlines()
    assert lines[0] == "0.4.0a2"
    assert lines[1] == "0.4.0a2"
    assert "site-packages" in lines[2].replace("\\", "/")
    assert not lines[2].lower().startswith(str(ROOT / "kairo_kernel").lower())


def test_runtime_requires_supported_python() -> None:
    assert sys.version_info >= (3, 11)
