"""Packaging metadata sanity (no wheel build inside the test)."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)["project"]
    assert metadata["name"] == "kairo-tui"
    assert metadata["version"] if "version" in metadata else True  # noqa: SIM401
    assert metadata["requires-python"] == ">=3.11"
    assert metadata["dependencies"] == [
        "textual>=8.2,<9",
        "rich>=14,<15",
        "keyring>=25,<26",
        "platformdirs>=4,<5",
        "kairo-kernel==0.4.0a2",
    ]
    assert metadata["scripts"] == {
        "kairo": "kairo_tui.cli:main",
        "kairo-tui": "kairo_tui.cli:main",
    }
