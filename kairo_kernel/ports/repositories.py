"""Persistence and workspace repository ports."""

from __future__ import annotations

from typing import Protocol

from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.support import ConfigSnapshot, SessionRecord, SessionSummary, WorkspaceRecord
from kairo_kernel.errors import KernelResult


class SessionRepositoryPort(Protocol):
    async def list(self) -> tuple[SessionSummary, ...]: ...

    async def load(self, session_id: SessionId) -> KernelResult[SessionRecord]: ...

    async def save(self, session: SessionRecord, active: bool) -> KernelResult[SessionRecord]: ...

    async def delete(self, session_id: SessionId) -> KernelResult[bool]: ...


class ConfigRepositoryPort(Protocol):
    async def load(self) -> KernelResult[ConfigSnapshot]: ...

    async def validate(self, snapshot: ConfigSnapshot) -> KernelResult[ConfigSnapshot]: ...

    async def save(self, snapshot: ConfigSnapshot, create_backup: bool = True) -> KernelResult[ConfigSnapshot]: ...

    async def restore(self, revision: int) -> KernelResult[ConfigSnapshot]: ...


class WorkspaceRepositoryPort(Protocol):
    async def current(self) -> WorkspaceRecord: ...

    async def validate(self, root: str) -> KernelResult[WorkspaceRecord]: ...

    async def apply(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]: ...

    async def rollback(self, workspace: WorkspaceRecord) -> KernelResult[WorkspaceRecord]: ...

