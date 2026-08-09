from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.support import WorkspaceRecord
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.services.workspaces import ChangedFile, WorkspaceService, _parse_porcelain

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git executable is required")


class FakeWorkspaceRepository:
    def __init__(self, root: Path) -> None:
        self.record = WorkspaceRecord(str(root.resolve()), 0)

    async def current(self) -> WorkspaceRecord:
        return self.record

    async def validate(self, root: str) -> KernelResult[WorkspaceRecord]:
        return KernelResult.failure(KernelError(ErrorCode.WORKSPACE_INVALID, "unused"))

    async def apply(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.record = workspace
        return KernelResult.success(workspace)

    async def rollback(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.record = workspace
        return KernelResult.success(workspace)


def _service(root: Path) -> WorkspaceService:
    return WorkspaceService(FakeWorkspaceRepository(root), WorkspaceLeaseManager(str(root.resolve())))


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *arguments],
        check=True,
        capture_output=True,
    )


def test_tree_lists_one_level_with_kinds_and_limit(tmp_path: Path) -> None:
    async def exercise() -> None:
        (tmp_path / "dir").mkdir()
        (tmp_path / "b.txt").write_text("bb", encoding="utf-8")
        (tmp_path / "a.txt").write_text("aaaa", encoding="utf-8")
        service = _service(tmp_path)

        tree = await service.tree(".")
        assert tree.ok and tree.value is not None
        assert tree.value.revision == 0
        assert [entry.name for entry in tree.value.entries] == ["a.txt", "b.txt", "dir"]
        assert tree.value.entries[0].size_bytes == 4
        assert tree.value.entries[2].is_directory
        assert not tree.value.truncated

        limited = await service.tree(".", limit=2)
        assert limited.ok and limited.value is not None
        assert limited.value.truncated and len(limited.value.entries) == 2

        escaped = await service.tree("..")
        assert escaped.error is not None and escaped.error.code is ErrorCode.WORKSPACE_INVALID

    asyncio.run(exercise())


def test_parse_porcelain_maps_status_letters() -> None:
    raw = b" M src/a.py\0?? new.txt\0R  dst.py\0src.py\0A  added.py\0D  gone.py\0"
    assert _parse_porcelain(raw) == (
        ChangedFile("src/a.py", "modified"),
        ChangedFile("new.txt", "untracked"),
        ChangedFile("dst.py", "renamed"),
        ChangedFile("added.py", "added"),
        ChangedFile("gone.py", "deleted"),
    )
    assert _parse_porcelain(b"") == ()


@needs_git
def test_changed_files_reports_repo_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = _service(tmp_path)
        plain = await service.changed_files()
        assert plain.ok and plain.value is not None
        assert not plain.value.is_git_repository and plain.value.files == ()

        _git(tmp_path, "init")
        (tmp_path / "tracked.txt").write_text("one", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "initial")
        (tmp_path / "tracked.txt").write_text("two", encoding="utf-8")
        (tmp_path / "fresh.txt").write_text("new", encoding="utf-8")

        changed = await service.changed_files()
        assert changed.ok and changed.value is not None
        assert changed.value.is_git_repository
        by_path = {entry.relative_path: entry.status for entry in changed.value.files}
        assert by_path["tracked.txt"] == "modified"
        assert by_path["fresh.txt"] == "untracked"

    asyncio.run(exercise())


@needs_git
def test_diff_returns_unified_diff_and_untracked_marker(tmp_path: Path) -> None:
    async def exercise() -> None:
        _git(tmp_path, "init")
        (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "initial")
        (tmp_path / "tracked.txt").write_text("two\n", encoding="utf-8")
        (tmp_path / "fresh.txt").write_text("new\n", encoding="utf-8")
        service = _service(tmp_path)

        diff = await service.diff("tracked.txt")
        assert diff.ok and diff.value is not None
        assert diff.value.status == "modified"
        assert "@@" in diff.value.unified_diff and "+two" in diff.value.unified_diff
        assert not diff.value.truncated

        untracked = await service.diff("fresh.txt")
        assert untracked.ok and untracked.value is not None
        assert untracked.value.status == "untracked" and untracked.value.unified_diff == ""

        escaped = await service.diff("../outside.txt")
        assert escaped.error is not None and escaped.error.code is ErrorCode.WORKSPACE_INVALID

    asyncio.run(exercise())
