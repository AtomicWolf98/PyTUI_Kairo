"""settings_model unit test: ``provider_rows`` / ``role_rows`` formatting.

The provider catalog snapshot is a kernel service DTO (boundary-forbidden), so
the model reads it structurally; the fake below mirrors only the accessed
surface (``.profiles`` / ``.roles``).
"""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile

from kairo_tui.settings_model import provider_rows, role_rows

PROFILE = ProviderProfile(
    ProfileId("openai:gpt"),
    "OpenAI / GPT",
    "openai_responses",
    "gpt-5.2",
    "https://api.openai.com/v1",
    32000,
    1000,
    0.2,
)


@dataclass(frozen=True)
class _Role:
    role: str
    profile_id: ProfileId


@dataclass(frozen=True)
class _FakeCatalog:
    revision: int
    profiles: tuple[ProviderProfile, ...]
    roles: tuple[_Role, ...]


def test_provider_and_role_rows_format_snapshot_rows() -> None:
    fake = _FakeCatalog(3, (PROFILE,), (_Role("chat", PROFILE.profile_id),))
    assert provider_rows(fake) == ("OpenAI / GPT (openai:gpt) — openai_responses/gpt-5.2",)
    assert role_rows(fake) == ("chat → openai:gpt",)
