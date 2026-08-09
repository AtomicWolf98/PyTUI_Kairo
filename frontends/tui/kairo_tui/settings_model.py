"""Pure view-model for the Settings page.

Textual-free row formatters for the provider catalog snapshot. The catalog
(``ProviderCatalogSnapshot``) is a kernel service DTO — boundary-forbidden —
so both functions read it structurally through the attributes the screen
actually consumes (``.profiles`` / ``.roles``).
"""

from __future__ import annotations


def provider_rows(snapshot) -> tuple[str, ...]:
    return tuple(f"{p.label} ({p.profile_id}) — {p.provider}/{p.model}" for p in snapshot.profiles)


def role_rows(snapshot) -> tuple[str, ...]:
    return tuple(f"{m.role} → {m.profile_id}" for m in snapshot.roles)
