"""Runtime preference port consumed by the turn engine."""

from typing import Protocol

from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.preferences import PreferencesSnapshot
from kairo_kernel.errors import KernelResult


class PreferencesPort(Protocol):
    async def snapshot(self) -> PreferencesSnapshot: ...

    async def apply_authorization(self, mode: AuthorizationMode) -> KernelResult[PreferencesSnapshot]: ...
