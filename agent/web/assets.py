"""Resolve WebUI assets shipped inside the Kairo package."""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def static_root() -> Path:
    """Return the package-owned WebUI root, whether or not it is complete."""
    return Path(str(resources.files("agent.web").joinpath("static")))
