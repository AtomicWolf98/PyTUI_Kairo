"""Keyring/env secret store; kernel SecretPort bridge."""

from __future__ import annotations

import asyncio

from kairo_kernel.contracts.identifiers import SecretId
from kairo_kernel.contracts.support import SecretInput

from kairo_tui.keyring_store import (
    KeyringSecretPort,
    SecretNotStored,
    SecretStore,
    env_name_for_secret,
    make_secret_store,
)


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_env_name_is_deterministic_and_sanitized() -> None:
    assert env_name_for_secret("openai:gpt-5") == "KAIRO_SECRET_OPENAI_GPT_5"


def test_keyring_backend_describe_store_resolve_delete() -> None:
    store = SecretStore(FakeBackend())
    assert store.available[0] is True
    secret_id = SecretId("openai")
    assert store.describe(secret_id).present is False
    store.store(secret_id, "sk-secret-value")
    reference = store.describe(secret_id)
    assert reference.source == "keyring" and reference.present is True
    assert store.resolve(secret_id) == "sk-secret-value"
    assert store.delete(secret_id) is True
    assert store.resolve(secret_id) is None


def test_env_fallback_resolves_environment_value(monkeypatch) -> None:
    store = SecretStore(None)
    assert store.available == (False, "env (no keyring backend)")
    secret_id = SecretId("openai")
    monkeypatch.setenv(env_name_for_secret(str(secret_id)), "sk-env-value")
    assert store.describe(secret_id).source == "env"
    assert store.resolve(secret_id) == "sk-env-value"


def test_env_fallback_store_requires_existing_env_var(monkeypatch) -> None:
    store = SecretStore(None)
    try:
        store.store(SecretId("openai"), "sk-value")
        raise AssertionError("store() must not persist plaintext via env fallback")
    except SecretNotStored:
        pass
    monkeypatch.setenv("KAIRO_SECRET_OPENAI", "sk-value")
    reference = store.store(SecretId("openai"), "sk-value")
    assert reference.source == "env" and reference.present is True


def test_safe_mode_denies_writes(monkeypatch) -> None:
    # Deterministic env-fallback regardless of whether a real keyring exists
    # (e.g. Windows Credential Locker) on the host; brief: keyring is monkeypatched.
    monkeypatch.setattr("kairo_tui.keyring_store.probe_keyring", lambda: (None, "env"))
    store = make_secret_store(safe_mode=True)
    assert store.available[0] is False
    try:
        store.store(SecretId("openai"), "sk-value")
        raise AssertionError("safe mode must deny secret writes")
    except SecretNotStored:
        pass


def test_secret_port_bridge_maps_results() -> None:
    port = KeyringSecretPort(SecretStore(FakeBackend()))
    stored = None
    async def exercise() -> None:
        nonlocal stored
        result = await port.store(SecretInput(SecretId("openai"), "sk-value"))
        assert result.ok and result.value is not None and result.value.present
        stored = result.value
        resolved = await port.resolve(SecretId("openai"))
        assert resolved.ok and resolved.value == "sk-value"
        missing = await port.resolve(SecretId("nope"))
        assert missing.error is not None and missing.error.code.value == "not_found"
    asyncio.run(exercise())


def test_secret_values_never_appear_in_repr() -> None:
    store = SecretStore(FakeBackend())
    secret_id = SecretId("openai")
    store.store(secret_id, "SUPER-SECRET-MARKER")
    rendered = repr(store.describe(secret_id)) + repr(KeyringSecretPort(store))
    assert "SUPER-SECRET-MARKER" not in rendered
