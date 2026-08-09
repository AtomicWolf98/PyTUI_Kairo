"""Secret scan: no full key material in documents, repr, or exports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kairo_kernel.contracts.identifiers import ProfileId, SecretId
from kairo_kernel.contracts.json import thaw_json
from kairo_kernel.contracts.providers import ProviderProfile
from kairo_kernel.contracts.support import SecretInput

from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter
from kairo_tui.keyring_store import KeyringSecretPort, SecretStore
from kairo_tui.store import AppState

MARKER = "sk-very-secret-marker-9f2c"


class MarkerBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_document_json_contains_no_secret_value(tmp_path: Path) -> None:
    store = SecretStore(MarkerBackend())
    store.store(SecretId("openai"), MARKER)
    document = ConfigDocument(
        profiles=(ProviderProfile(ProfileId("p1"), "M", "openai_responses", "gpt-5.2",
                                  "https://api.openai.com/v1", 32000, 1000, 0.2, secret_id="openai"),),
    )
    path = tmp_path / "config-v1.json"
    ConfigDocumentAdapter(path).save(document)
    assert MARKER not in path.read_text(encoding="utf-8")


def test_repr_of_store_and_state_never_exposes_secret() -> None:
    store = SecretStore(MarkerBackend())
    store.store(SecretId("openai"), MARKER)
    secret_input = SecretInput(SecretId("openai"), MARKER)
    state = AppState(workspace_root="C:/ws")
    assert MARKER not in repr(store.describe(SecretId("openai")))
    assert MARKER not in repr(secret_input)
    assert MARKER not in repr(state)


def test_port_resolve_is_only_place_value_flows() -> None:
    port = KeyringSecretPort(SecretStore(MarkerBackend()))
    stored = asyncio.run(port.store(SecretInput(SecretId("openai"), MARKER)))
    assert stored.ok and stored.value is not None
    value = stored.value
    rendered = json.dumps(thaw_json(value.to_json_value())) if hasattr(value, "to_json_value") else str(value)
    assert MARKER not in rendered
