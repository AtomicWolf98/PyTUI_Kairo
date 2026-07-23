"""Fail-closed trust records for workspace-provided Python skills.

Workspace skills are executable code.  Their trust decision therefore lives
outside the workspace and is bound to both the canonical workspace path and a
digest of the complete skills directory.  A workspace cannot grant trust to
itself by editing an adjacent metadata file.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


TRUST_STORE_VERSION = 1
WINDOWS_REPARSE_POINT = 0x400


class SkillTrustError(ValueError):
    """Raised when a skill path or trust request is invalid."""


@dataclass(frozen=True)
class SkillCandidate:
    """A discovered, but not necessarily trusted, workspace skill."""

    workspace_root: str
    skills_root: str
    relative_path: str
    digest: str
    trusted: bool
    status: str = "pending"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "skills_root": self.skills_root,
            "relative_path": self.relative_path,
            "digest": self.digest,
            "trusted": self.trusted,
            "status": self.status,
            "reason": self.reason,
        }


def default_skill_trust_path() -> Path:
    """Return the per-user trust-store path, outside any workspace."""
    override = os.environ.get("KAIRO_SKILL_TRUST_STORE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return (Path(base) / "Kairo" / "skill-trust.json").resolve()
        return (Path.home() / "AppData" / "Local" / "Kairo" / "skill-trust.json").resolve()
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if base:
        return (Path(base).expanduser() / "kairo" / "skill-trust.json").resolve()
    return (Path.home() / ".config" / "kairo" / "skill-trust.json").resolve()


def _canonical_path(path: Path) -> str:
    value = str(path.expanduser().resolve())
    return os.path.normcase(value) if os.name == "nt" else value


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & WINDOWS_REPARSE_POINT)
    except OSError:
        return True


def _iter_manifest_files(root: Path) -> Iterable[Path]:
    """Yield regular files without following links/reparse points."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise SkillTrustError(f"Cannot inspect skills directory '{directory}': {exc}") from exc
        for child in children:
            if _is_link_or_reparse(child):
                raise SkillTrustError(f"Links and reparse points are not allowed in skills: {child}")
            if child.is_dir():
                if child.name == "__pycache__":
                    continue
                pending.append(child)
            elif child.is_file() and child.suffix not in {".pyc", ".pyo"}:
                yield child
            else:
                raise SkillTrustError(f"Unsupported entry in skills directory: {child}")


def directory_manifest_snapshot(root: Path) -> tuple[str, dict[str, bytes]]:
    """Read one stable manifest snapshot and return its digest and file bytes."""
    root = root.resolve()
    digest = hashlib.sha256()
    files = sorted(_iter_manifest_files(root), key=lambda item: item.relative_to(root).as_posix())
    snapshot: dict[str, bytes] = {}
    for path in files:
        relative_text = path.relative_to(root).as_posix()
        relative = relative_text.encode("utf-8")
        try:
            file_bytes = path.read_bytes()
        except OSError as exc:
            raise SkillTrustError(f"Cannot read skill manifest entry '{path}': {exc}") from exc
        snapshot[relative_text] = file_bytes
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(len(file_bytes)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(file_bytes).digest())
    return digest.hexdigest(), snapshot


def directory_manifest_digest(root: Path) -> str:
    """Hash names, sizes and contents for every stable file in *root*."""
    digest, _ = directory_manifest_snapshot(root)
    return digest


class SkillTrustStore:
    """Atomic external store for explicit workspace-skill trust."""

    def __init__(self, path: Optional[Path] = None):
        self.path = (path or default_skill_trust_path()).expanduser().resolve()
        self.warnings: list[str] = []

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": TRUST_STORE_VERSION, "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
                raise ValueError("trust store is not a valid JSON object")
            if data.get("version") != TRUST_STORE_VERSION:
                raise ValueError(f"unsupported trust store version: {data.get('version')}")
            return data
        except Exception as exc:
            self.warnings.append(f"Failed to load skill trust store: {exc}")
            return {"version": TRUST_STORE_VERSION, "entries": []}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _ensure_store_outside_workspace(self, workspace: Path) -> None:
        try:
            self.path.relative_to(workspace)
        except ValueError:
            return
        raise SkillTrustError(
            f"Skill trust store must be outside the workspace: {self.path}"
        )

    @staticmethod
    def resolve_skills_path(workspace_root: Path, skills_dir: str) -> tuple[Path, Path]:
        workspace = Path(workspace_root).expanduser().resolve()
        if not workspace.is_dir():
            raise SkillTrustError(f"Workspace is not a directory: {workspace}")

        configured = Path(skills_dir).expanduser()
        lexical = configured if configured.is_absolute() else workspace / configured

        # Inspect lexical components before resolve() so links cannot disappear
        # behind their target path.
        try:
            relative_lexical = lexical.absolute().relative_to(workspace)
        except ValueError as exc:
            raise SkillTrustError(f"Skills directory is outside workspace: {lexical}") from exc
        cursor = workspace
        for part in relative_lexical.parts:
            cursor = cursor / part
            if cursor.exists() and _is_link_or_reparse(cursor):
                raise SkillTrustError(f"Links and reparse points are not allowed in skills path: {cursor}")

        resolved = lexical.resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise SkillTrustError(f"Skills directory is outside workspace: {resolved}") from exc
        return workspace, resolved

    @staticmethod
    def _entry_key(workspace_root: str, relative_path: str) -> tuple[str, str]:
        return workspace_root, relative_path.replace("\\", "/")

    def discover(self, workspace_root: Path, skills_dir: str) -> list[SkillCandidate]:
        """Discover top-level Python skills without importing them."""
        workspace, skills_root = self.resolve_skills_path(workspace_root, skills_dir)
        self._ensure_store_outside_workspace(workspace)
        if not skills_root.exists():
            return []
        if not skills_root.is_dir() or _is_link_or_reparse(skills_root):
            raise SkillTrustError(f"Skills path is not a safe directory: {skills_root}")

        manifest_digest = directory_manifest_digest(skills_root)
        workspace_key = _canonical_path(workspace)
        entries = self._load().get("entries", [])
        trusted_entries = {
            self._entry_key(str(entry.get("workspace_root", "")), str(entry.get("relative_path", ""))):
            str(entry.get("digest", ""))
            for entry in entries
            if isinstance(entry, dict)
        }

        candidates: list[SkillCandidate] = []
        for py_file in sorted(skills_root.glob("*.py"), key=lambda item: item.name.casefold()):
            if py_file.name == "__init__.py":
                continue
            if _is_link_or_reparse(py_file) or not stat.S_ISREG(py_file.stat().st_mode):
                raise SkillTrustError(f"Skill must be a regular non-link file: {py_file}")
            relative_path = py_file.relative_to(skills_root).as_posix()
            recorded_digest = trusted_entries.get(self._entry_key(workspace_key, relative_path))
            trusted = recorded_digest == manifest_digest
            status = "trusted" if trusted else ("changed" if recorded_digest else "pending")
            candidates.append(
                SkillCandidate(
                    workspace_root=workspace_key,
                    skills_root=str(skills_root),
                    relative_path=relative_path,
                    digest=manifest_digest,
                    trusted=trusted,
                    status=status,
                    reason=(
                        ""
                        if trusted
                        else (
                            "The skills manifest changed after trust was granted."
                            if status == "changed"
                            else "Explicit trust is required."
                        )
                    ),
                )
            )
        return candidates

    def trust(
        self,
        workspace_root: Path,
        skills_dir: str,
        relative_path: str,
        expected_digest: str,
    ) -> SkillCandidate:
        """Persist trust only if the caller-confirmed digest is still current."""
        normalized = str(relative_path).replace("\\", "/")
        candidate = next(
            (item for item in self.discover(workspace_root, skills_dir) if item.relative_path == normalized),
            None,
        )
        if candidate is None:
            raise SkillTrustError(f"Skill was not found: {relative_path}")
        if not expected_digest or candidate.digest != expected_digest:
            raise SkillTrustError("Skill manifest changed after it was reviewed; review and trust it again.")

        data = self._load()
        entries = [
            entry
            for entry in data.get("entries", [])
            if not (
                isinstance(entry, dict)
                and self._entry_key(
                    str(entry.get("workspace_root", "")),
                    str(entry.get("relative_path", "")),
                )
                == self._entry_key(candidate.workspace_root, candidate.relative_path)
            )
        ]
        entries.append(
            {
                "workspace_root": candidate.workspace_root,
                "relative_path": candidate.relative_path,
                "digest": candidate.digest,
            }
        )
        entries.sort(key=lambda entry: (entry["workspace_root"], entry["relative_path"]))
        self._save({"version": TRUST_STORE_VERSION, "entries": entries})
        return SkillCandidate(
            workspace_root=candidate.workspace_root,
            skills_root=candidate.skills_root,
            relative_path=candidate.relative_path,
            digest=candidate.digest,
            trusted=True,
            status="trusted",
        )

    def trust_all(
        self,
        workspace_root: Path,
        skills_dir: str,
        expected_digest: str,
    ) -> list[SkillCandidate]:
        """Atomically trust every skill in one reviewed directory manifest."""
        candidates = self.discover(workspace_root, skills_dir)
        if not candidates:
            raise SkillTrustError("No custom skills were found to trust.")
        current_digest = candidates[0].digest
        if (
            not expected_digest
            or current_digest != expected_digest
            or any(candidate.digest != current_digest for candidate in candidates)
        ):
            raise SkillTrustError("Skill manifest changed after it was reviewed; review and trust it again.")

        workspace_key = candidates[0].workspace_root
        data = self._load()
        entries = [
            entry
            for entry in data.get("entries", [])
            if not (
                isinstance(entry, dict)
                and str(entry.get("workspace_root", "")) == workspace_key
            )
        ]
        entries.extend(
            {
                "workspace_root": candidate.workspace_root,
                "relative_path": candidate.relative_path,
                "digest": current_digest,
            }
            for candidate in candidates
        )
        entries.sort(key=lambda entry: (entry["workspace_root"], entry["relative_path"]))
        self._save({"version": TRUST_STORE_VERSION, "entries": entries})
        return [
            SkillCandidate(
                workspace_root=candidate.workspace_root,
                skills_root=candidate.skills_root,
                relative_path=candidate.relative_path,
                digest=candidate.digest,
                trusted=True,
                status="trusted",
            )
            for candidate in candidates
        ]

    def revoke(self, workspace_root: Path, skills_dir: str, relative_path: str) -> bool:
        workspace, _ = self.resolve_skills_path(workspace_root, skills_dir)
        self._ensure_store_outside_workspace(workspace)
        workspace_key = _canonical_path(workspace)
        relative_key = str(relative_path).replace("\\", "/")
        data = self._load()
        old_entries = data.get("entries", [])
        new_entries = [
            entry
            for entry in old_entries
            if not (
                isinstance(entry, dict)
                and self._entry_key(
                    str(entry.get("workspace_root", "")),
                    str(entry.get("relative_path", "")),
                )
                == self._entry_key(workspace_key, relative_key)
            )
        ]
        if len(new_entries) == len(old_entries):
            return False
        self._save({"version": TRUST_STORE_VERSION, "entries": new_entries})
        return True

    def revoke_all(self, workspace_root: Path, skills_dir: str) -> bool:
        """Atomically revoke every custom skill for the workspace."""
        workspace, _ = self.resolve_skills_path(workspace_root, skills_dir)
        self._ensure_store_outside_workspace(workspace)
        workspace_key = _canonical_path(workspace)
        data = self._load()
        old_entries = data.get("entries", [])
        new_entries = [
            entry
            for entry in old_entries
            if not (
                isinstance(entry, dict)
                and str(entry.get("workspace_root", "")) == workspace_key
            )
        ]
        if len(new_entries) == len(old_entries):
            return False
        self._save({"version": TRUST_STORE_VERSION, "entries": new_entries})
        return True
