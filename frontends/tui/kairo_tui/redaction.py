"""Redaction of copied diagnostics: mask present secret values, never persist them.

The kernel's DiagnosticReport is not redacted (verified), so the TUI redacts on
copy. ``secret_markers`` resolves present secret values (keyring via the
SecretStore + ``KAIRO_SECRET_*`` env vars) into masking markers; values flow
into memory only to be replaced and are never persisted or logged.
"""

from __future__ import annotations

import os

from kairo_kernel.contracts.identifiers import SecretId

from kairo_tui.keyring_store import ENV_PREFIX, SecretStore


def secret_markers(store: SecretStore, secret_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve present secret values (keyring + KAIRO_SECRET_* env) to mask.

    Values flow into memory only to be replaced; the returned markers are
    never persisted or logged.
    """
    markers: list[str] = []
    for secret_id in secret_ids:
        value = store.resolve(SecretId(secret_id))
        if value and len(value) >= 4:
            markers.append(value)
    for name, value in os.environ.items():
        if name.startswith(ENV_PREFIX) and value and len(value) >= 4:
            markers.append(value)
    return tuple(sorted(set(markers)))


def redact_text(text: str, markers: tuple[str, ...]) -> str:
    redacted = text
    for marker in markers:
        redacted = redacted.replace(marker, "********")
    return redacted
