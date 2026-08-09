"""Validate Kairo 0.4.0a2 release metadata and wheel payloads (kernel + TUI)."""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "0.4.0a2"

# distribution name -> (version-module source, pyproject, expected wheel prefix)
PACKAGES = {
    "kairo-kernel": (ROOT / "kairo_kernel" / "_version.py",
                     ROOT / "pyproject.toml",
                     "kairo_kernel-0.4.0a2-py3-none-any.whl"),
    "kairo-tui": (ROOT / "frontends" / "tui" / "kairo_tui" / "_version.py",
                  ROOT / "frontends" / "tui" / "pyproject.toml",
                  "kairo_tui-0.4.0a2-py3-none-any.whl"),
}


def source_version() -> str:            # kernel
    return _module_version(PACKAGES["kairo-kernel"][0])


def tui_version() -> str:              # TUI
    return _module_version(PACKAGES["kairo-tui"][0])


def _module_version(path: Path) -> str:
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                      path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError(f"{path} does not define __version__")
    return match.group(1)


def check_source_tree() -> str:
    versions = {name: _module_version(version) for name, (version, _, _) in PACKAGES.items()}
    if set(versions.values()) != {RELEASE}:
        raise RuntimeError(f"expected version {RELEASE}; got {versions}")
    expected_attr = {
        "kairo-kernel": "kairo_kernel._version.__version__",
        "kairo-tui": "kairo_tui._version.__version__",
    }
    for name, (_, pyproject, _) in PACKAGES.items():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        dynamic = project["project"].get("dynamic", [])
        attr = project["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        if "version" not in dynamic or attr != expected_attr[name]:
            raise RuntimeError(f"{name} pyproject must derive its version from {expected_attr[name]}")
    return RELEASE


def check_wheel(wheel: Path, expected_version: str, namespace: str) -> None:
    """The wheel must carry ONLY ``namespace/`` + its own ``.dist-info/``."""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        dist_infos = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(dist_infos) != 1:
            raise RuntimeError(f"{wheel.name} has an unexpected METADATA layout")
        metadata = Parser().parsestr(archive.read(dist_infos[0]).decode("utf-8"))
        if metadata.get("Version") != expected_version:
            raise RuntimeError(
                f"{wheel.name} metadata version {metadata.get('Version')!r} does not match {expected_version!r}"
            )
        allowed = (f"{namespace}/", f"{namespace}-{expected_version}.dist-info/")
        offenders = sorted(name for name in names if not name.startswith(allowed))
        if offenders:
            raise RuntimeError(f"{wheel.name} contains unexpected entries: {', '.join(offenders)}")
        for forbidden in ("agent/", "tools/", "web/", "tests/"):
            if any(name.startswith(forbidden) for name in names):
                raise RuntimeError(f"{wheel.name} must not ship legacy {forbidden.strip('/')} content")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", action="append", type=Path)
    args = parser.parse_args(argv)
    try:
        version = check_source_tree()
        for wheel in args.wheel or []:
            namespace = wheel.name.split("-", 1)[0]
            check_wheel(wheel.resolve(), version, namespace)
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"release-check: {exc}", file=sys.stderr)
        return 1
    print(f"release-check: Kairo {version} ({' + '.join(sorted(PACKAGES))}) sources and wheel payloads are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
