"""Revisioned runtime preferences; patches affect only turns accepted afterwards."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from kairo_kernel.contracts.enums import AuthorizationMode, ErrorCode
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
from kairo_kernel.errors import KernelError, KernelResult


class PreferencesService:
    """Hold the current runtime preference snapshot under one lock."""

    def __init__(self, initial: PreferencesSnapshot) -> None:
        self._snapshot = initial
        self._lock = asyncio.Lock()

    async def snapshot(self) -> PreferencesSnapshot:
        async with self._lock:
            return self._snapshot

    async def patch(self, patch: PreferencesPatch) -> KernelResult[PreferencesSnapshot]:
        async with self._lock:
            if patch.expected_revision != self._snapshot.revision:
                return _failure(ErrorCode.CONFLICT, "Preferences revision has changed.", "preferences.patch")
            current = self._snapshot
            try:
                candidate = replace(
                    current,
                    revision=current.revision + 1,
                    authorization_mode=(
                        current.authorization_mode if patch.authorization_mode is None else patch.authorization_mode
                    ),
                    plan_mode=current.plan_mode if patch.plan_mode is None else patch.plan_mode,
                    thinking_mode=current.thinking_mode if patch.thinking_mode is None else patch.thinking_mode,
                    context_trigger_percent=(
                        current.context_trigger_percent
                        if patch.context_trigger_percent is None
                        else patch.context_trigger_percent
                    ),
                    context_target_percent=(
                        current.context_target_percent
                        if patch.context_target_percent is None
                        else patch.context_target_percent
                    ),
                    preserve_recent_turns=(
                        current.preserve_recent_turns
                        if patch.preserve_recent_turns is None
                        else patch.preserve_recent_turns
                    ),
                    profile_id=(
                        None
                        if patch.clear_profile_id
                        else (current.profile_id if patch.profile_id is None else patch.profile_id)
                    ),
                )
            except ValueError as exc:
                return _failure(ErrorCode.CONFIG_INVALID, f"Preferences patch is invalid: {exc}", "preferences.patch")
            self._snapshot = candidate
            return KernelResult.success(candidate)

    async def apply_authorization(self, mode: AuthorizationMode) -> KernelResult[PreferencesSnapshot]:
        """Engine-internal durable authorization change (ENABLE_AUTO / ENABLE_YOLO)."""

        async with self._lock:
            updated = replace(self._snapshot, revision=self._snapshot.revision + 1, authorization_mode=mode)
            self._snapshot = updated
            return KernelResult.success(updated)


def _failure(code: ErrorCode, message: str, operation: str) -> KernelResult[PreferencesSnapshot]:
    return KernelResult.failure(KernelError(code, message, operation=operation))
