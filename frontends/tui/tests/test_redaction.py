"""Redaction unit tests: marker masking and secret resolution.

``secret_markers`` resolves present secret values (keyring + ``KAIRO_SECRET_*``
env vars) into masking markers; values flow into memory only to be replaced and
are never persisted or logged. ``redact_text`` replaces every occurrence of a
marker so copied diagnostics never carry full secret material.
"""

from __future__ import annotations

from kairo_tui.keyring_store import SecretStore
from kairo_tui.redaction import redact_text, secret_markers


class _MemoryBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_redact_text_masks_a_marker_everywhere() -> None:
    text = "request key sk-live-9f2c; retry key sk-live-9f2c"
    assert redact_text(text, ("sk-live-9f2c",)) == "request key ********; retry key ********"


def test_redact_text_leaves_ordinary_text_untouched() -> None:
    text = "Kairo diagnostics (local) — ok"
    assert redact_text(text, ("sk-live-9f2c",)) == text


def test_secret_markers_returns_env_values_and_skips_short_values(monkeypatch) -> None:
    monkeypatch.setenv("KAIRO_SECRET_PILOT", "sk-pilot-1234")
    markers = secret_markers(SecretStore(None), ())
    assert "sk-pilot-1234" in markers
    # Values shorter than 4 characters are never masked.
    monkeypatch.setenv("KAIRO_SECRET_SHORT", "abc")
    assert "abc" not in secret_markers(SecretStore(None), ())
