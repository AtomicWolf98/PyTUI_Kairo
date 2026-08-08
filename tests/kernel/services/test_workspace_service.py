from __future__ import annotations

import asyncio
from pathlib import Path

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.support import WorkspaceRecord
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.services.workspaces import (
    InMemoryWorkspaceBookmarks,
    WorkspaceBookmark,
    WorkspaceParticipant,
    WorkspaceService,
)


class FakeWorkspaceRepository:
    def __init__(self, root: Path) -> None:
        self.record = WorkspaceRecord(str(root.resolve()), 0)
        self.fail_apply = False
        self.fail_rollback = False
        self.applied: list[WorkspaceRecord] = []
        self.rolled_back: list[WorkspaceRecord] = []

    async def current(self) -> WorkspaceRecord:
        return self.record

    async def validate(self, root: str) -> KernelResult[WorkspaceRecord]:
        path = Path(root).resolve()
        if not path.is_dir():
            return KernelResult.failure(KernelError(ErrorCode.WORKSPACE_INVALID, "invalid"))
        return KernelResult.success(WorkspaceRecord(str(path), self.record.revision + 1, self.record.root))

    async def apply(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.applied.append(workspace)
        if self.fail_apply:
            return KernelResult.failure(KernelError(ErrorCode.RUNTIME_SYNC_FAILED, "raw repository detail"))
        self.record = workspace
        return KernelResult.success(workspace)

    async def rollback(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]:
        self.rolled_back.append(workspace)
        if self.fail_rollback:
            return KernelResult.failure(KernelError(ErrorCode.RUNTIME_SYNC_FAILED, "rollback detail"))
        self.record = workspace
        return KernelResult.success(workspace)


class FakeParticipant(WorkspaceParticipant):
    def __init__(self, *, fail_apply: bool = False, fail_rollback: bool = False) -> None:
        self.fail_apply = fail_apply
        self.fail_rollback = fail_rollback
        self.applied: list[WorkspaceRecord] = []
        self.rolled_back: list[WorkspaceRecord] = []

    async def apply_workspace(self, workspace: WorkspaceRecord) -> None:
        self.applied.append(workspace)
        if self.fail_apply:
            raise RuntimeError("participant apply failed")

    async def rollback_workspace(self, workspace: WorkspaceRecord) -> None:
        self.rolled_back.append(workspace)
        if self.fail_rollback:
            raise RuntimeError("participant rollback failed")


class FakeDegraded:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def mark_degraded(self, reason: str) -> None:
        self.reasons.append(reason)


def service_for(
    root: Path,
    *,
    bookmarks: InMemoryWorkspaceBookmarks | None = None,
    participants: tuple[WorkspaceParticipant, ...] = (),
    degraded: FakeDegraded | None = None,
    preview_limit: int = 8,
) -> tuple[WorkspaceService, FakeWorkspaceRepository]:
    repository = FakeWorkspaceRepository(root)
    service = WorkspaceService(
        repository,
        WorkspaceLeaseManager(str(root.resolve())),
        bookmarks=bookmarks,
        participants=participants,
        degraded=degraded,
        preview_limit_bytes=preview_limit,
        preview_child_limit=2,
    )
    return service, repository


async def test_snapshot_preview_bookmarks_and_revision_conflict(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("abcdefghijk", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    bookmarks = InMemoryWorkspaceBookmarks((WorkspaceBookmark("home", str(tmp_path)),))
    service, _ = service_for(tmp_path, bookmarks=bookmarks)

    snapshot = await service.snapshot()
    assert snapshot.revision == 0
    assert snapshot.bookmarks == (WorkspaceBookmark("home", str(tmp_path)),)

    file_preview = await service.preview("alpha.txt")
    assert file_preview.ok and file_preview.value is not None
    assert file_preview.value.text == "abcdefgh"
    assert file_preview.value.truncated
    directory_preview = await service.preview(".")
    assert directory_preview.ok and directory_preview.value is not None
    assert len(directory_preview.value.children) == 2
    assert directory_preview.value.truncated

    escaped = await service.preview("../outside.txt")
    assert not escaped.ok and escaped.error is not None
    assert escaped.error.code is ErrorCode.WORKSPACE_INVALID
    conflict = await service.save_bookmark("next", str(tmp_path), expected_revision=3)
    assert not conflict.ok and conflict.error is not None
    assert conflict.error.code is ErrorCode.CONFLICT


async def test_move_uses_bookmark_and_turn_snapshot_isolation(tmp_path: Path) -> None:
    target = tmp_path / "next"
    target.mkdir()
    bookmarks = InMemoryWorkspaceBookmarks((WorkspaceBookmark("next", str(target)),))
    service, repository = service_for(tmp_path, bookmarks=bookmarks)

    async with service.turn_snapshot() as pinned:
        assert pinned.revision == 0
        move_task = asyncio.create_task(service.move("next", expected_revision=0))
        await asyncio.sleep(0)
        assert not move_task.done()

    moved = await move_task
    assert moved.ok and moved.value is not None
    assert moved.value.root == str(target.resolve())
    assert moved.value.revision == 1
    assert repository.record == WorkspaceRecord(str(target.resolve()), 1, str(tmp_path.resolve()))


async def test_move_rolls_back_and_rollback_failure_marks_degraded(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    first = FakeParticipant()
    second = FakeParticipant(fail_apply=True)
    degraded = FakeDegraded()
    service, repository = service_for(tmp_path, participants=(first, second), degraded=degraded)
    repository.fail_rollback = True

    result = await service.move(str(target), expected_revision=0)

    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.KERNEL_DEGRADED
    assert first.rolled_back == [WorkspaceRecord(str(tmp_path.resolve()), 0)]
    assert degraded.reasons == ["Workspace rollback failed."]
    blocked = await service.save_bookmark("blocked", str(target), expected_revision=0)
    assert not blocked.ok and blocked.error is not None
    assert blocked.error.code is ErrorCode.KERNEL_DEGRADED


async def test_bookmark_update_is_revisioned_and_rolls_back_store(tmp_path: Path) -> None:
    bookmarks = FailingBookmarkStore()
    service, repository = service_for(tmp_path, bookmarks=bookmarks)

    result = await service.save_bookmark("repo", str(tmp_path), expected_revision=0)

    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.RUNTIME_SYNC_FAILED
    assert repository.record.revision == 0
    assert repository.rolled_back == [WorkspaceRecord(str(tmp_path.resolve()), 0)]


class FailingBookmarkStore(InMemoryWorkspaceBookmarks):
    async def replace(self, bookmarks: tuple[WorkspaceBookmark, ...]) -> None:
        raise RuntimeError("bookmark store failed")
