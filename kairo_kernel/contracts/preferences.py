"""Revisioned runtime turn preferences; changes affect only future turns."""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.enums import AuthorizationMode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.json import Contract


@dataclass(frozen=True)
class PreferencesSnapshot(Contract):
    revision: int
    authorization_mode: AuthorizationMode = AuthorizationMode.MANUAL
    plan_mode: bool = False
    thinking_mode: bool = True
    context_trigger_percent: float = 85.0
    context_target_percent: float = 60.0
    preserve_recent_turns: int = 4
    profile_id: ProfileId | None = None

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("Preferences revision cannot be negative.")
        if not 1.0 <= self.context_trigger_percent <= 100.0:
            raise ValueError("context_trigger_percent must be within 1..100.")
        if not 1.0 <= self.context_target_percent <= self.context_trigger_percent:
            raise ValueError("context_target_percent must be within 1..trigger.")
        if self.preserve_recent_turns < 0:
            raise ValueError("preserve_recent_turns cannot be negative.")


@dataclass(frozen=True)
class PreferencesPatch(Contract):
    expected_revision: int
    authorization_mode: AuthorizationMode | None = None
    plan_mode: bool | None = None
    thinking_mode: bool | None = None
    context_trigger_percent: float | None = None
    context_target_percent: float | None = None
    preserve_recent_turns: int | None = None
    profile_id: ProfileId | None = None
    clear_profile_id: bool = False

    def __post_init__(self) -> None:
        if self.clear_profile_id and self.profile_id is not None:
            raise ValueError("clear_profile_id and profile_id cannot both be set.")
