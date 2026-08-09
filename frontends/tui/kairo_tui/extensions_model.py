"""Pure view-model for the Extensions page.

Textual-free: ``tool_rows`` flattens built-in tool descriptors into row
previews, ``skill_rows`` flattens a skill inventory into package rows, and
``mcp_entries``/``mcp_groups`` turn the flat MCP catalog into namespace →
sorted qualified-name groups. Only public ``kairo_kernel.contracts.tools``
types are touched directly; the skill and MCP DTOs are boundary-forbidden, so
they are read structurally through the ``structs`` Protocols (boundary test:
no kernel-private imports).
"""

from __future__ import annotations

from typing import cast

from kairo_kernel.contracts.tools import ToolDescriptor

from kairo_tui.structs import CatalogEntryLike, SkillInventoryLike


def tool_rows(descriptors: tuple[ToolDescriptor, ...]) -> tuple[str, ...]:
    return tuple(f"{d.name} — {d.description}" for d in descriptors)


def skill_rows(inventory: SkillInventoryLike) -> tuple[str, ...]:
    return tuple(
        f"{p.manifest.name} ({p.relative_path}) — {p.manifest.description}"
        for p in inventory.packages
    )


def mcp_entries(catalog: tuple[object, ...]) -> tuple[CatalogEntryLike, ...]:
    return tuple(cast(CatalogEntryLike, entry) for entry in catalog)


def mcp_groups(entries: tuple[CatalogEntryLike, ...]) -> dict[str, tuple[str, ...]]:
    """namespace → sorted qualified names."""
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(entry.namespace, []).append(entry.qualified_name)
    return {ns: tuple(sorted(names)) for ns, names in groups.items()}
