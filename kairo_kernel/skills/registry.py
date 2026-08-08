"""Fail-closed skill discovery, trust, reload and revocation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from kairo_kernel.skills.manifest import SkillPackage, packages_from_snapshot, snapshot_directory
from kairo_kernel.skills.trust import SkillTrustStore


@dataclass(frozen=True)
class SkillInventory:
    digest: str
    status: str
    packages: tuple[SkillPackage, ...]


class SkillRegistry:
    def __init__(self, workspace: Path, skills_directory: str, trust_store: SkillTrustStore):
        self.workspace = workspace.expanduser().resolve()
        candidate = self.workspace / skills_directory
        self.skills_root = candidate.resolve()
        try:
            self.skills_root.relative_to(self.workspace)
        except ValueError as error:
            raise ValueError("Skills directory must stay inside the workspace.") from error
        self.trust_store = trust_store
        self._active: tuple[SkillPackage, ...] = ()
        self._lock = asyncio.Lock()

    async def inspect(self) -> SkillInventory:
        if not self.skills_root.exists():
            return SkillInventory("", "absent", ())
        snapshot = await asyncio.to_thread(snapshot_directory, self.skills_root)
        packages = packages_from_snapshot(snapshot)
        trusted = self.trust_store.trusted_digest(self.workspace)
        status = "trusted" if trusted == snapshot.digest else ("changed" if trusted else "untrusted")
        return SkillInventory(snapshot.digest, status, packages)

    async def trust(self, expected_digest: str) -> SkillInventory:
        async with self._lock:
            await asyncio.to_thread(
                self.trust_store.trust,
                self.workspace,
                self.skills_root,
                expected_digest,
            )
            return await self._reload_locked()

    async def reload(self) -> SkillInventory:
        async with self._lock:
            return await self._reload_locked()

    async def revoke(self) -> bool:
        async with self._lock:
            revoked = await asyncio.to_thread(self.trust_store.revoke, self.workspace)
            self._active = ()
            return revoked

    async def active(self) -> tuple[SkillPackage, ...]:
        async with self._lock:
            return self._active

    async def _reload_locked(self) -> SkillInventory:
        before = await asyncio.to_thread(snapshot_directory, self.skills_root)
        trusted = self.trust_store.trusted_digest(self.workspace)
        if before.digest != trusted:
            self._active = ()
            status = "changed" if trusted else "untrusted"
            return SkillInventory(before.digest, status, ())
        packages = packages_from_snapshot(before)
        after = await asyncio.to_thread(snapshot_directory, self.skills_root)
        if after.digest != before.digest:
            self._active = ()
            return SkillInventory(after.digest, "changed", ())
        self._active = packages
        return SkillInventory(before.digest, "trusted", packages)
