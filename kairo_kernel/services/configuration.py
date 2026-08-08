"""Revisioned configuration validation, redaction and transactions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.json import JsonArray, JsonMember, JsonObject, JsonValue, freeze_json, thaw_json
from kairo_kernel.contracts.support import ConfigSnapshot
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports.repositories import ConfigRepositoryPort


class ConfigValueKind(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    NULL = "null"


@dataclass(frozen=True)
class ConfigField:
    path: tuple[str, ...]
    kind: ConfigValueKind
    required: bool = False
    secret: bool = False

    def __post_init__(self) -> None:
        if not self.path or any(not item.strip() for item in self.path):
            raise ValueError("Config field paths must contain non-empty segments.")


@dataclass(frozen=True)
class ConfigSchema:
    fields: tuple[ConfigField, ...]
    allow_unknown: bool = True


@dataclass(frozen=True)
class ConfigChange:
    path: tuple[str, ...]
    value: JsonValue = None
    remove: bool = False

    def __post_init__(self) -> None:
        if not self.path or any(not item.strip() for item in self.path):
            raise ValueError("Config change paths must contain non-empty segments.")


@dataclass(frozen=True)
class ConfigPatch:
    expected_revision: int
    changes: tuple[ConfigChange, ...]


@dataclass(frozen=True)
class ConfigBackup:
    revision: int


class ConfigurationParticipant(Protocol):
    async def apply_configuration(self, snapshot: ConfigSnapshot) -> None: ...

    async def rollback_configuration(self, snapshot: ConfigSnapshot) -> None: ...


class DegradedSignal(Protocol):
    async def mark_degraded(self, reason: str) -> None: ...


class ConfigurationService:
    """Own raw configuration while exposing only redacted state."""

    def __init__(
        self,
        repository: ConfigRepositoryPort,
        initial: ConfigSnapshot,
        schema: ConfigSchema,
        *,
        participants: tuple[ConfigurationParticipant, ...] = (),
        degraded: DegradedSignal | None = None,
    ) -> None:
        raw = ConfigSnapshot(initial.revision, initial.values, redacted=False)
        self._repository = repository
        self._schema = schema
        self._participants = participants
        self._degraded = degraded
        self._leases = _ConfigLeaseManager(raw)
        self._degraded_reason = ""
        self._mutation_lock = asyncio.Lock()

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason

    @classmethod
    async def open(
        cls,
        repository: ConfigRepositoryPort,
        schema: ConfigSchema,
        *,
        default_values: JsonObject = JsonObject(),
        participants: tuple[ConfigurationParticipant, ...] = (),
        degraded: DegradedSignal | None = None,
    ) -> KernelResult[ConfigurationService]:
        loaded = await repository.load()
        if loaded.ok:
            assert loaded.value is not None
            initial = loaded.value
        elif loaded.error is not None and loaded.error.code is ErrorCode.NOT_FOUND:
            initial = ConfigSnapshot(0, default_values, redacted=False)
        else:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    "Configuration could not be loaded.",
                    retryable=True,
                    operation="config.open",
                )
            )
        service = cls(repository, initial, schema, participants=participants, degraded=degraded)
        validation = service._validate(initial.values, initial.revision)
        if not validation.ok:
            assert validation.error is not None
            return KernelResult.failure(validation.error)
        return KernelResult.success(service)

    async def snapshot(self) -> ConfigSnapshot:
        lease = await self._leases.read()
        async with lease:
            return self._redact(lease.snapshot)

    @asynccontextmanager
    async def turn_snapshot(self) -> AsyncIterator[ConfigSnapshot]:
        """Pin the exact configuration revision used by one turn."""

        lease = await self._leases.read()
        async with lease:
            yield lease.snapshot

    async def validate(self, values: JsonObject) -> KernelResult[ConfigSnapshot]:
        snapshot = await self._leases.snapshot()
        result = self._validate(values, snapshot.revision)
        if not result.ok:
            return result
        assert result.value is not None
        return KernelResult.success(self._redact(result.value))

    async def patch(self, patch: ConfigPatch) -> KernelResult[ConfigSnapshot]:
        if not patch.changes:
            return _config_failure(ErrorCode.INVALID_ARGUMENT, "Configuration patch is empty.", "config.patch")
        return await self._mutate(
            patch.expected_revision,
            "config.patch",
            lambda current: _apply_changes(current, patch.changes),
        )

    async def import_json(self, payload: str, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        try:
            loaded = freeze_json(json.loads(payload))
        except (json.JSONDecodeError, TypeError, ValueError):
            return _config_failure(ErrorCode.CONFIG_INVALID, "Configuration import is not valid JSON.", "config.import")
        if not isinstance(loaded, JsonObject):
            return _config_failure(ErrorCode.CONFIG_INVALID, "Configuration import must be an object.", "config.import")
        return await self._mutate(expected_revision, "config.import", lambda current: loaded)

    async def export_json(self) -> str:
        snapshot = await self.snapshot()
        return json.dumps(thaw_json(snapshot.values), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    async def backup(self) -> KernelResult[ConfigBackup]:
        lease = await self._leases.read()
        async with lease:
            snapshot = lease.snapshot
            saved = await self._repository.save(snapshot, create_backup=True)
        if not saved.ok:
            return KernelResult.failure(
                KernelError(
                    ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    "Configuration backup failed.",
                    retryable=saved.error.retryable if saved.error is not None else False,
                    operation="config.backup",
                )
            )
        return KernelResult.success(ConfigBackup(snapshot.revision))

    async def restore(self, backup: ConfigBackup, expected_revision: int) -> KernelResult[ConfigSnapshot]:
        if self._degraded_reason:
            return _config_failure(ErrorCode.KERNEL_DEGRADED, "Configuration mutations are disabled.", "config.restore")
        async with self._mutation_lock:
            lease = await self._leases.write()
            async with lease:
                if lease.snapshot.revision != expected_revision:
                    return _config_failure(ErrorCode.CONFLICT, "Configuration revision has changed.", "config.restore")
                old = lease.snapshot
                restored = await self._repository.restore(backup.revision)
                if not restored.ok:
                    return _config_failure(
                        restored.error.code if restored.error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                        "Configuration backup could not be restored.",
                        "config.restore",
                    )
                assert restored.value is not None
                candidate = ConfigSnapshot(old.revision + 1, restored.value.values, redacted=False)
                validation = self._validate(candidate.values, candidate.revision)
                if not validation.ok:
                    rollback = await self._repository.restore(old.revision)
                    if not rollback.ok:
                        await self._mark_degraded("Configuration rollback failed.")
                        return _config_failure(
                            ErrorCode.KERNEL_DEGRADED,
                            "Configuration restore failed and recovery was incomplete.",
                            "config.restore",
                        )
                    return validation
                return await self._commit_locked(lease, old, candidate, "config.restore")

    async def _mutate(
        self,
        expected_revision: int,
        operation: str,
        mutate: ConfigMutation,
    ) -> KernelResult[ConfigSnapshot]:
        if self._degraded_reason:
            return _config_failure(ErrorCode.KERNEL_DEGRADED, "Configuration mutations are disabled.", operation)
        async with self._mutation_lock:
            lease = await self._leases.write()
            async with lease:
                if lease.snapshot.revision != expected_revision:
                    return _config_failure(ErrorCode.CONFLICT, "Configuration revision has changed.", operation)
                old = lease.snapshot
                try:
                    values = mutate(old.values)
                except (KeyError, TypeError, ValueError):
                    return _config_failure(ErrorCode.CONFIG_INVALID, "Configuration patch is invalid.", operation)
                candidate = ConfigSnapshot(old.revision + 1, values, redacted=False)
                validation = self._validate(candidate.values, candidate.revision)
                if not validation.ok:
                    return validation
                repository_validation = await self._repository.validate(candidate)
                if not repository_validation.ok:
                    return _config_failure(ErrorCode.CONFIG_INVALID, "Configuration validation failed.", operation)
                return await self._commit_locked(lease, old, candidate, operation)

    async def _commit_locked(
        self,
        lease: _ConfigLease,
        old: ConfigSnapshot,
        candidate: ConfigSnapshot,
        operation: str,
    ) -> KernelResult[ConfigSnapshot]:
        applied: list[ConfigurationParticipant] = []
        try:
            saved = await self._repository.save(candidate, create_backup=True)
            if not saved.ok:
                raise _ConfigTransactionFailure(saved.error)
            for participant in self._participants:
                applied.append(participant)
                await participant.apply_configuration(candidate)
            await self._leases.update(lease, candidate)
            return KernelResult.success(self._redact(candidate))
        except Exception as exc:
            rollback_ok = await self._rollback(old, tuple(applied))
            if not rollback_ok:
                await self._mark_degraded("Configuration rollback failed.")
                return _config_failure(
                    ErrorCode.KERNEL_DEGRADED,
                    "Configuration update failed and recovery was incomplete.",
                    operation,
                )
            if isinstance(exc, _ConfigTransactionFailure):
                return _config_failure(
                    exc.error.code if exc.error is not None else ErrorCode.CONFIG_PERSISTENCE_FAILED,
                    "Configuration persistence failed.",
                    operation,
                    retryable=exc.error.retryable if exc.error is not None else False,
                )
            return _config_failure(ErrorCode.RUNTIME_SYNC_FAILED, "Configuration runtime update failed.", operation)

    async def _rollback(
        self,
        old: ConfigSnapshot,
        applied: tuple[ConfigurationParticipant, ...],
    ) -> bool:
        ok = True
        for participant in reversed(applied):
            try:
                await participant.rollback_configuration(old)
            except Exception:
                ok = False
        restored = await self._repository.restore(old.revision)
        ok = ok and restored.ok
        return ok

    async def _mark_degraded(self, reason: str) -> None:
        self._degraded_reason = reason
        if self._degraded is not None:
            await self._degraded.mark_degraded(reason)

    def _validate(self, values: JsonObject, revision: int) -> KernelResult[ConfigSnapshot]:
        known = {field.path for field in self._schema.fields}
        for field in self._schema.fields:
            present, value = _lookup(values, field.path)
            if field.required and not present:
                return _config_failure(
                    ErrorCode.CONFIG_INVALID,
                    f"Required configuration field '{'.'.join(field.path)}' is missing.",
                    "config.validate",
                )
            if present and not _matches(value, field.kind):
                return _config_failure(
                    ErrorCode.CONFIG_INVALID,
                    f"Configuration field '{'.'.join(field.path)}' has the wrong type.",
                    "config.validate",
                )
        if not self._schema.allow_unknown:
            for path in _leaf_paths(values):
                if path not in known and not any(known_path[: len(path)] == path for known_path in known):
                    return _config_failure(
                        ErrorCode.CONFIG_INVALID,
                        f"Unknown configuration field '{'.'.join(path)}'.",
                        "config.validate",
                    )
        return KernelResult.success(ConfigSnapshot(revision, values, redacted=False))

    def _redact(self, snapshot: ConfigSnapshot) -> ConfigSnapshot:
        values = snapshot.values
        for field in self._schema.fields:
            if field.secret:
                present, _ = _lookup(values, field.path)
                if present:
                    values = _set_path(values, field.path, "[REDACTED]", False)
        return ConfigSnapshot(snapshot.revision, values, redacted=True)


class _ConfigMutation(Protocol):
    def __call__(self, current: JsonObject) -> JsonObject: ...


ConfigMutation = _ConfigMutation


class _ConfigTransactionFailure(RuntimeError):
    def __init__(self, error: KernelError | None) -> None:
        self.error = error
        super().__init__("Configuration transaction failed.")


class _ConfigLease:
    def __init__(self, manager: _ConfigLeaseManager, write: bool, snapshot: ConfigSnapshot) -> None:
        self._manager = manager
        self.write = write
        self.snapshot = snapshot
        self._released = False

    async def __aenter__(self) -> _ConfigLease:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.release()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._manager.release(self.write)


class _ConfigLeaseManager:
    def __init__(self, snapshot: ConfigSnapshot) -> None:
        self._snapshot = snapshot
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    async def read(self) -> _ConfigLease:
        async with self._condition:
            while self._writer or self._waiting_writers:
                await self._condition.wait()
            self._readers += 1
            return _ConfigLease(self, False, self._snapshot)

    async def write(self) -> _ConfigLease:
        async with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    await self._condition.wait()
                self._writer = True
                return _ConfigLease(self, True, self._snapshot)
            finally:
                self._waiting_writers -= 1
                self._condition.notify_all()

    async def update(self, lease: _ConfigLease, snapshot: ConfigSnapshot) -> None:
        async with self._condition:
            if lease._manager is not self or lease._released or not lease.write or not self._writer:
                raise RuntimeError("An active configuration write lease is required.")
            self._snapshot = snapshot

    async def snapshot(self) -> ConfigSnapshot:
        async with self._condition:
            return self._snapshot

    async def release(self, write: bool) -> None:
        async with self._condition:
            if write:
                self._writer = False
            else:
                self._readers -= 1
            self._condition.notify_all()


def _apply_changes(current: JsonObject, changes: tuple[ConfigChange, ...]) -> JsonObject:
    result = current
    for change in changes:
        result = _set_path(result, change.path, change.value, change.remove)
    return result


def _set_path(current: JsonObject, path: tuple[str, ...], value: JsonValue, remove: bool) -> JsonObject:
    head, *tail_items = path
    tail = tuple(tail_items)
    members = list(current.items)
    index = next((position for position, item in enumerate(members) if item.key == head), None)
    if not tail:
        if remove:
            if index is None:
                raise KeyError(head)
            members.pop(index)
        elif index is None:
            members.append(JsonMember(head, value))
        else:
            members[index] = JsonMember(head, value)
        return JsonObject(tuple(members))
    if index is None:
        if remove:
            raise KeyError(head)
        child = JsonObject()
    else:
        existing = members[index].value
        if not isinstance(existing, JsonObject):
            raise TypeError(head)
        child = existing
    nested = _set_path(child, tail, value, remove)
    replacement = JsonMember(head, nested)
    if index is None:
        members.append(replacement)
    else:
        members[index] = replacement
    return JsonObject(tuple(members))


def _lookup(values: JsonObject, path: tuple[str, ...]) -> tuple[bool, JsonValue]:
    current: JsonValue = values
    for segment in path:
        if not isinstance(current, JsonObject):
            return False, None
        found = next((item for item in current.items if item.key == segment), None)
        if found is None:
            return False, None
        current = found.value
    return True, current


def _matches(value: JsonValue, kind: ConfigValueKind) -> bool:
    if kind is ConfigValueKind.STRING:
        return isinstance(value, str)
    if kind is ConfigValueKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is ConfigValueKind.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind is ConfigValueKind.BOOLEAN:
        return isinstance(value, bool)
    if kind is ConfigValueKind.OBJECT:
        return isinstance(value, JsonObject)
    if kind is ConfigValueKind.ARRAY:
        return isinstance(value, JsonArray)
    return value is None


def _leaf_paths(values: JsonObject, prefix: tuple[str, ...] = ()) -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = []
    for member in values.items:
        path = prefix + (member.key,)
        if isinstance(member.value, JsonObject) and member.value.items:
            paths.extend(_leaf_paths(member.value, path))
        else:
            paths.append(path)
    return tuple(paths)


ResultT = TypeVar("ResultT")


def _config_failure(
    code: ErrorCode,
    message: str,
    operation: str,
    *,
    retryable: bool = False,
) -> KernelResult[ResultT]:
    return KernelResult.failure(KernelError(code, message, retryable, operation))
