"""Validate Kairo release metadata and wheel payloads."""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def source_version() -> str:
    text = (ROOT / "agent" / "_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeError("agent/_version.py does not define __version__")
    return match.group(1)


def check_source_tree() -> str:
    version = source_version()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dynamic = pyproject["project"].get("dynamic", [])
    version_attr = pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    if "version" not in dynamic or version_attr != "agent._version.__version__":
        raise RuntimeError("pyproject.toml must derive its version from agent._version.__version__")

    for relative in ("web/package.json", "web/package-lock.json"):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        if payload["version"] != version:
            raise RuntimeError(f"{relative} version {payload['version']!r} does not match {version!r}")

    static = ROOT / "agent" / "web" / "static"
    if not (static / "index.html").is_file():
        raise RuntimeError("agent/web/static/index.html is missing; run npm --prefix web run build")
    metadata = json.loads((static / "version.json").read_text(encoding="utf-8"))
    if metadata.get("version") != version:
        raise RuntimeError("built WebUI version does not match the package version")
    _check_html_assets((static / "index.html").read_text(encoding="utf-8"), static)
    return version


def check_wheel(wheel: Path, expected_version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        static_prefix = "agent/web/static/"
        index_name = f"{static_prefix}index.html"
        version_name = f"{static_prefix}version.json"
        if index_name not in names or version_name not in names:
            raise RuntimeError(f"{wheel.name} does not contain packaged WebUI entry points")

        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"{wheel.name} has an unexpected METADATA layout")
        package_metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        if package_metadata.get("Version") != expected_version:
            raise RuntimeError(
                f"{wheel.name} metadata version {package_metadata.get('Version')!r} "
                f"does not match {expected_version!r}"
            )

        static_metadata = json.loads(archive.read(version_name).decode("utf-8"))
        if static_metadata.get("version") != expected_version:
            raise RuntimeError(f"{wheel.name} contains mismatched WebUI release metadata")

        html = archive.read(index_name).decode("utf-8")
        referenced = _asset_references(html)
        expected_assets = {f"agent/web/static{asset}" for asset in referenced}
        missing = sorted(expected_assets - names)
        if missing:
            raise RuntimeError(f"{wheel.name} is missing referenced assets: {', '.join(missing)}")
        packaged_assets = {name for name in names if name.startswith(f"{static_prefix}assets/") and not name.endswith("/")}
        stale = sorted(packaged_assets - expected_assets)
        if stale:
            raise RuntimeError(f"{wheel.name} contains stale unreferenced assets: {', '.join(stale)}")


def _asset_references(html: str) -> list[str]:
    return sorted(set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html)))


def _check_html_assets(html: str, static: Path) -> None:
    referenced = _asset_references(html)
    if not referenced:
        raise RuntimeError("built WebUI does not reference any hashed assets")
    missing = [asset for asset in referenced if not (static / asset.lstrip("/")).is_file()]
    if missing:
        raise RuntimeError(f"built WebUI is missing referenced assets: {', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args(argv)
    try:
        version = check_source_tree()
        if args.wheel:
            check_wheel(args.wheel.resolve(), version)
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"release-check: {exc}", file=sys.stderr)
        return 1
    print(f"release-check: Kairo {version} source and payload metadata are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
