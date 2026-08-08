"""Immutable skill manifests and stable directory snapshots."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


class SkillManifestError(ValueError):
    """A skill directory is malformed or unsafe."""


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    entrypoint: str
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillPackage:
    relative_path: str
    manifest: SkillManifest
    manifest_digest: str
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class DirectorySnapshot:
    root: Path
    digest: str
    files: tuple[tuple[str, bytes], ...]

    def read(self, relative_path: str) -> bytes:
        normalized = relative_path.replace("\\", "/")
        for name, content in self.files:
            if name == normalized:
                return content
        raise SkillManifestError(f"Manifest file is missing: {relative_path}")


def snapshot_directory(root: Path) -> DirectorySnapshot:
    """Read every regular file without following links and hash names plus bytes."""
    resolved = root.expanduser().resolve()
    if not resolved.is_dir() or _is_link(resolved):
        raise SkillManifestError(f"Skill root is not a safe directory: {resolved}")
    paths = _manifest_paths(resolved)
    files: list[tuple[str, bytes]] = []
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(resolved).as_posix()
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SkillManifestError(f"Cannot read skill file '{relative}': {error}") from error
        files.append((relative, content))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return DirectorySnapshot(resolved, digest.hexdigest(), tuple(files))


def packages_from_snapshot(snapshot: DirectorySnapshot) -> tuple[SkillPackage, ...]:
    manifest_paths = sorted(name for name, _ in snapshot.files if name.endswith("/skill.json"))
    packages: list[SkillPackage] = []
    names: set[str] = set()
    for manifest_path in manifest_paths:
        root = manifest_path.rsplit("/", 1)[0]
        manifest = parse_manifest(snapshot.read(manifest_path))
        if manifest.name in names:
            raise SkillManifestError(f"Duplicate skill name: {manifest.name}")
        names.add(manifest.name)
        prefix = f"{root}/"
        files = tuple((name[len(prefix) :], content) for name, content in snapshot.files if name.startswith(prefix))
        if manifest.entrypoint not in {name for name, _ in files}:
            raise SkillManifestError(f"Skill '{manifest.name}' entrypoint is missing: {manifest.entrypoint}")
        packages.append(SkillPackage(root, manifest, snapshot.digest, files))
    return tuple(packages)


def parse_manifest(content: bytes) -> SkillManifest:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillManifestError(f"Invalid skill.json: {error}") from error
    if not isinstance(value, dict):
        raise SkillManifestError("skill.json must contain a JSON object.")
    name = value.get("name")
    description = value.get("description")
    entrypoint = value.get("entrypoint", "SKILL.md")
    permissions = value.get("permissions", [])
    if not isinstance(name, str) or not name or not _safe_name(name):
        raise SkillManifestError("Skill name must use letters, digits, dots, underscores or hyphens.")
    if not isinstance(description, str) or not description:
        raise SkillManifestError(f"Skill '{name}' requires a description.")
    if not isinstance(entrypoint, str) or not _safe_relative(entrypoint):
        raise SkillManifestError(f"Skill '{name}' has an unsafe entrypoint.")
    if not isinstance(permissions, list) or not all(isinstance(item, str) and item for item in permissions):
        raise SkillManifestError(f"Skill '{name}' permissions must be strings.")
    return SkillManifest(name, description, entrypoint, tuple(permissions))


def _manifest_paths(root: Path) -> list[Path]:
    output: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            raise SkillManifestError(f"Cannot inspect skill directory '{directory}': {error}") from error
        for child in children:
            if _is_link(child):
                raise SkillManifestError(f"Links and reparse points are forbidden in skills: {child}")
            if child.is_dir():
                if child.name != "__pycache__":
                    pending.append(child)
            elif child.is_file() and child.suffix not in {".pyc", ".pyo"}:
                output.append(child)
            else:
                raise SkillManifestError(f"Unsupported skill entry: {child}")
    return sorted(output, key=lambda path: path.relative_to(root).as_posix())


def _is_link(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & 0x400)
    except OSError:
        return True


def _safe_name(value: str) -> bool:
    return all(character.isalnum() or character in "._-" for character in value)


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and os.path.normpath(value) != "."
