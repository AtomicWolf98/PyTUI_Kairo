from __future__ import annotations

import asyncio

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.support import ConfigSnapshot
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.services.configuration import (
    ConfigChange,
    ConfigField,
    ConfigPatch,
    ConfigSchema,
    ConfigurationParticipant,
    ConfigurationService,
    ConfigValueKind,
)


def object_value(value: object) -> JsonObject:
    frozen = freeze_json(value)
    assert isinstance(frozen, JsonObject)
    return frozen


SCHEMA = ConfigSchema(
    (
        ConfigField(("llm", "model"), ConfigValueKind.STRING, required=True),
        ConfigField(("llm", "api_key"), ConfigValueKind.STRING, required=True, secret=True),
        ConfigField(("limits", "max_turns"), ConfigValueKind.INTEGER),
    )
)


class FakeConfigRepository:
    def __init__(self, initial: ConfigSnapshot) -> None:
        self.current = initial
        self.revisions = {initial.revision: initial}
        self.fail_save = False
        self.fail_restore = False

    async def load(self) -> KernelResult[ConfigSnapshot]:
        return KernelResult.success(self.current)

    async def validate(self, snapshot: ConfigSnapshot) -> KernelResult[ConfigSnapshot]:
        return KernelResult.success(snapshot)

    async def save(self, snapshot: ConfigSnapshot, create_backup: bool = True) -> KernelResult[ConfigSnapshot]:
        del create_backup
        if self.fail_save:
            return KernelResult.failure(KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "contains hunter2"))
        self.current = snapshot
        self.revisions[snapshot.revision] = snapshot
        return KernelResult.success(snapshot)

    async def restore(self, revision: int) -> KernelResult[ConfigSnapshot]:
        if self.fail_restore or revision not in self.revisions:
            return KernelResult.failure(KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, "contains hunter2"))
        self.current = self.revisions[revision]
        return KernelResult.success(self.current)


class FakeParticipant(ConfigurationParticipant):
    def __init__(self, *, fail_apply: bool = False) -> None:
        self.fail_apply = fail_apply
        self.applied: list[ConfigSnapshot] = []
        self.rolled_back: list[ConfigSnapshot] = []

    async def apply_configuration(self, snapshot: ConfigSnapshot) -> None:
        self.applied.append(snapshot)
        if self.fail_apply:
            raise RuntimeError("runtime contains hunter2")

    async def rollback_configuration(self, snapshot: ConfigSnapshot) -> None:
        self.rolled_back.append(snapshot)


class FakeDegraded:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    async def mark_degraded(self, reason: str) -> None:
        self.reasons.append(reason)


def initial_snapshot() -> ConfigSnapshot:
    return ConfigSnapshot(
        0,
        object_value({"llm": {"model": "small", "api_key": "hunter2"}, "limits": {"max_turns": 4}}),
        redacted=False,
    )


async def test_validate_snapshot_export_and_errors_never_expose_secret() -> None:
    initial = initial_snapshot()
    service = ConfigurationService(FakeConfigRepository(initial), initial, SCHEMA)

    snapshot = await service.snapshot()
    exported = await service.export_json()

    assert snapshot.redacted
    assert thaw_json(snapshot.values) == {
        "llm": {"model": "small", "api_key": "[REDACTED]"},
        "limits": {"max_turns": 4},
    }
    assert "hunter2" not in exported
    invalid = await service.validate(object_value({"llm": {"model": "small", "api_key": 42}}))
    assert not invalid.ok and invalid.error is not None
    assert invalid.error.code is ErrorCode.CONFIG_INVALID
    assert "hunter2" not in invalid.error.to_json()


async def test_typed_patch_revision_and_turn_snapshot_isolation() -> None:
    initial = initial_snapshot()
    service = ConfigurationService(FakeConfigRepository(initial), initial, SCHEMA)

    async with service.turn_snapshot() as pinned:
        patch_task = asyncio.create_task(
            service.patch(ConfigPatch(0, (ConfigChange(("llm", "model"), "large"),)))
        )
        await asyncio.sleep(0)
        assert not patch_task.done()
        assert thaw_json(pinned.values)["llm"]["model"] == "small"  # type: ignore[index]

    result = await patch_task
    assert result.ok and result.value is not None
    assert result.value.revision == 1
    assert thaw_json(result.value.values)["llm"]["model"] == "large"  # type: ignore[index]
    conflict = await service.patch(ConfigPatch(0, (ConfigChange(("limits", "max_turns"), 10),)))
    assert not conflict.ok and conflict.error is not None
    assert conflict.error.code is ErrorCode.CONFLICT


async def test_import_backup_restore_and_monotonic_revision() -> None:
    initial = initial_snapshot()
    service = ConfigurationService(FakeConfigRepository(initial), initial, SCHEMA)

    imported = await service.import_json(
        '{"llm":{"model":"medium","api_key":"new-secret"},"limits":{"max_turns":8}}',
        expected_revision=0,
    )
    assert imported.ok and imported.value is not None
    assert "new-secret" not in imported.value.to_json()
    backup = await service.backup()
    assert backup.ok and backup.value is not None
    changed = await service.patch(ConfigPatch(1, (ConfigChange(("llm", "model"), "large"),)))
    assert changed.ok

    restored = await service.restore(backup.value, expected_revision=2)
    assert restored.ok and restored.value is not None
    assert restored.value.revision == 3
    assert thaw_json(restored.value.values)["llm"]["model"] == "medium"  # type: ignore[index]


async def test_rollback_failure_marks_degraded_and_sanitizes_failure() -> None:
    initial = initial_snapshot()
    repository = FakeConfigRepository(initial)
    repository.fail_restore = True
    participant = FakeParticipant(fail_apply=True)
    degraded = FakeDegraded()
    service = ConfigurationService(repository, initial, SCHEMA, participants=(participant,), degraded=degraded)

    result = await service.patch(ConfigPatch(0, (ConfigChange(("llm", "model"), "broken"),)))

    assert not result.ok and result.error is not None
    assert result.error.code is ErrorCode.KERNEL_DEGRADED
    assert "hunter2" not in result.error.to_json()
    assert degraded.reasons == ["Configuration rollback failed."]
    blocked = await service.import_json("{}", expected_revision=0)
    assert not blocked.ok and blocked.error is not None
    assert blocked.error.code is ErrorCode.KERNEL_DEGRADED
