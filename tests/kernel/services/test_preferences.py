from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import AuthorizationMode, ErrorCode
from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.preferences import PreferencesPatch, PreferencesSnapshot
from kairo_kernel.services.preferences import PreferencesService


def test_patch_merges_and_bumps_revision() -> None:
    async def exercise() -> None:
        service = PreferencesService(PreferencesSnapshot(0))
        patched = await service.patch(
            PreferencesPatch(0, authorization_mode=AuthorizationMode.AUTO, plan_mode=True, profile_id=ProfileId("p/m"))
        )
        assert patched.ok and patched.value is not None
        assert patched.value.revision == 1
        assert patched.value.authorization_mode is AuthorizationMode.AUTO
        assert patched.value.plan_mode is True
        assert patched.value.thinking_mode is True  # unchanged default
        assert patched.value.profile_id == ProfileId("p/m")

    asyncio.run(exercise())


def test_patch_conflict_and_invalid_ranges_are_typed() -> None:
    async def exercise() -> None:
        service = PreferencesService(PreferencesSnapshot(0))
        conflict = await service.patch(PreferencesPatch(3, plan_mode=True))
        assert conflict.error is not None and conflict.error.code is ErrorCode.CONFLICT

        bad_trigger = await service.patch(PreferencesPatch(0, context_trigger_percent=30.0))
        assert bad_trigger.error is not None and bad_trigger.error.code is ErrorCode.CONFIG_INVALID

        bad_preserve = await service.patch(PreferencesPatch(0, preserve_recent_turns=-1))
        assert bad_preserve.error is not None and bad_preserve.error.code is ErrorCode.CONFIG_INVALID
        assert (await service.snapshot()).revision == 0

    asyncio.run(exercise())


def test_apply_authorization_bumps_revision_without_expected_revision() -> None:
    async def exercise() -> None:
        service = PreferencesService(PreferencesSnapshot(0))
        applied = await service.apply_authorization(AuthorizationMode.YOLO)
        assert applied.ok and applied.value is not None
        assert applied.value.authorization_mode is AuthorizationMode.YOLO
        assert applied.value.revision == 1

    asyncio.run(exercise())


def test_snapshot_contract_rejects_invalid_construction() -> None:
    import pytest

    with pytest.raises(ValueError):
        PreferencesSnapshot(0, context_target_percent=99.0)  # target must be <= trigger (85)
    with pytest.raises(ValueError):
        PreferencesSnapshot(-1)


def test_patch_clear_profile_id_resets_profile() -> None:
    async def exercise() -> None:
        service = PreferencesService(PreferencesSnapshot(0, profile_id=ProfileId("p/m")))
        patched = await service.patch(PreferencesPatch(0, clear_profile_id=True))
        assert patched.ok and patched.value is not None
        assert patched.value.profile_id is None
        assert patched.value.revision == 1
        assert patched.value.authorization_mode is AuthorizationMode.MANUAL  # unchanged

    asyncio.run(exercise())


def test_patch_clear_and_profile_together_are_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        PreferencesPatch(0, profile_id=ProfileId("p/m"), clear_profile_id=True)
