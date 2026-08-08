"""External, digest-bound trust records for workspace skills."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from kairo_kernel.skills.manifest import SkillManifestError, snapshot_directory


class SkillTrustStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def trusted_digest(self, workspace: Path) -> str:
        key = _workspace_key(workspace)
        entries = self._load()
        value = entries.get(key, "")
        return value if isinstance(value, str) else ""

    def trust(self, workspace: Path, skills_root: Path, expected_digest: str) -> str:
        _outside_workspace(self.path, workspace)
        current = snapshot_directory(skills_root).digest
        if not expected_digest or current != expected_digest:
            raise SkillManifestError("Skill manifest changed after review; review and trust it again.")
        entries = self._load()
        entries[_workspace_key(workspace)] = current
        self._save(entries)
        return current

    def revoke(self, workspace: Path) -> bool:
        _outside_workspace(self.path, workspace)
        entries = self._load()
        removed = entries.pop(_workspace_key(workspace), None) is not None
        if removed:
            self._save(entries)
        return removed

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillManifestError(f"Cannot load skill trust store: {error}") from error
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("entries"), dict):
            raise SkillManifestError("Skill trust store is invalid.")
        entries = value["entries"]
        assert isinstance(entries, dict)
        return dict(entries)

    def _save(self, entries: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump({"version": 1, "entries": entries}, handle, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _workspace_key(workspace: Path) -> str:
    value = str(workspace.expanduser().resolve())
    return os.path.normcase(value) if os.name == "nt" else value


def _outside_workspace(path: Path, workspace: Path) -> None:
    try:
        path.relative_to(workspace.expanduser().resolve())
    except ValueError:
        return
    raise SkillManifestError("Skill trust store must be outside the workspace.")
