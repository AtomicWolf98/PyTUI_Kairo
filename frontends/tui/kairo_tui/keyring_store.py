"""Secret storage: keyring first, env-var reference fallback, never plaintext.

Security contract (tui_plan.md): the keyring service is fixed to ``kairo`` and
the account is the ``secret_id``. When no keyring backend is usable, the TUI
only ever stores an *environment-variable reference* (``KAIRO_SECRET_<ID>``);
the value must already be present in the environment. Secret values never reach
the config document, logs, repr() output, or snapshots.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import SecretId
from kairo_kernel.contracts.support import SecretDescriptor, SecretInput
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.ports import SecretPort

KEYRING_SERVICE = "kairo"
ENV_PREFIX = "KAIRO_SECRET_"


def env_name_for_secret(secret_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", secret_id).strip("_")
    return f"{ENV_PREFIX}{safe.upper()}"


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


def probe_keyring() -> tuple[KeyringBackend | None, str]:
    """Return a usable keyring backend or (None, reason). Never raises."""
    try:
        import keyring

        backend = keyring.get_keyring()
        name = str(getattr(backend, "name", "") or type(backend).__name__).lower()
        module = type(backend).__module__.lower()
        if name in ("fail", "null") or "fail" in module or "null" in module:
            return None, "keyring backend unavailable"
        return backend, "keyring"
    except Exception as exc:  # pragma: no cover - platform-dependent
        return None, f"keyring unavailable: {type(exc).__name__}"


@dataclass(frozen=True)
class SecretReference:
    secret_id: SecretId
    source: str  # "keyring" | "env"
    env_var: str  # non-empty in env mode
    present: bool


class SecretNotStored(RuntimeError):
    """Raised when a secret cannot be stored without writing plaintext."""


class SecretStore:
    def __init__(self, backend: KeyringBackend | None, *, read_only: bool = False) -> None:
        self._backend = backend
        self._read_only = read_only

    @property
    def available(self) -> tuple[bool, str]:
        return (self._backend is not None, "keyring" if self._backend else "env (no keyring backend)")

    def describe(self, secret_id: SecretId) -> SecretReference:
        if self._backend is not None:
            present = self._backend.get_password(KEYRING_SERVICE, str(secret_id)) is not None
            return SecretReference(secret_id, "keyring", "", present)
        variable = env_name_for_secret(str(secret_id))
        return SecretReference(secret_id, "env", variable, variable in os.environ)

    def resolve(self, secret_id: SecretId) -> str | None:
        if self._backend is not None:
            return self._backend.get_password(KEYRING_SERVICE, str(secret_id))
        return os.environ.get(env_name_for_secret(str(secret_id)))

    def store(self, secret_id: SecretId, value: str) -> SecretReference:
        if self._read_only:
            raise SecretNotStored("Safe mode denies secret writes.")
        if self._backend is not None:
            self._backend.set_password(KEYRING_SERVICE, str(secret_id), value)
            return SecretReference(secret_id, "keyring", "", True)
        variable = env_name_for_secret(str(secret_id))
        if variable not in os.environ:
            raise SecretNotStored(
                f"No keyring backend is available; set {variable} and retry (no plaintext is ever written to disk)."
            )
        return SecretReference(secret_id, "env", variable, True)

    def delete(self, secret_id: SecretId) -> bool:
        if self._read_only:
            return False
        if self._backend is not None:
            self._backend.delete_password(KEYRING_SERVICE, str(secret_id))
            return True
        variable = env_name_for_secret(str(secret_id))
        if variable in os.environ:
            del os.environ[variable]
            return True
        return False


def make_secret_store(*, safe_mode: bool) -> SecretStore:
    backend, _reason = probe_keyring()
    return SecretStore(backend, read_only=safe_mode)


class KeyringSecretPort(SecretPort):
    """Adapt the TUI SecretStore to the kernel's public SecretPort."""

    def __init__(self, store: SecretStore) -> None:
        self._store = store

    async def describe(self, secret_id: SecretId) -> KernelResult[SecretDescriptor]:
        reference = self._store.describe(secret_id)
        return KernelResult.success(
            SecretDescriptor(secret_id, reference.source, "********" if reference.present else "", reference.present)
        )

    async def resolve(self, secret_id: SecretId) -> KernelResult[str]:
        value = self._store.resolve(secret_id)
        if value is None:
            return KernelResult.failure(KernelError(ErrorCode.NOT_FOUND, "Secret was not found."))
        return KernelResult.success(value)

    async def store(self, secret: SecretInput) -> KernelResult[SecretDescriptor]:
        try:
            reference = self._store.store(secret.secret_id, secret.value)
        except SecretNotStored as exc:
            return KernelResult.failure(
                KernelError(ErrorCode.CONFIG_PERSISTENCE_FAILED, str(exc))
            )
        return KernelResult.success(SecretDescriptor(secret.secret_id, reference.source, "********", True))

    async def delete(self, secret_id: SecretId) -> KernelResult[bool]:
        return KernelResult.success(self._store.delete(secret_id))
