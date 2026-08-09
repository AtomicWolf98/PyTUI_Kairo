"""Extensions model unit tests: tool_rows, skill_rows and mcp_groups formatting.

All three are pure functions over the brief's TUI-local Protocols; the skill
and MCP inputs are fakes (SimpleNamespace) because the real DTOs live in
boundary-forbidden kernel modules.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.tools import ToolDescriptor

from kairo_tui.extensions_model import mcp_groups, skill_rows, tool_rows
from kairo_tui.structs import CatalogEntryLike, SkillInventoryLike


def test_tool_rows_formats_name_and_description() -> None:
    descriptors = (
        ToolDescriptor("read_file", "Read a file from the workspace.", JsonObject(), ("workspace:read",)),
        ToolDescriptor("search", "Full-text search across files.", JsonObject(), ("workspace:read",)),
    )
    assert tool_rows(descriptors) == (
        "read_file — Read a file from the workspace.",
        "search — Full-text search across files.",
    )


def test_skill_rows_formats_packages_from_fake_inventory() -> None:
    inventory = cast(
        SkillInventoryLike,
        SimpleNamespace(
            digest="abc123",
            status="trusted",
            packages=(
                SimpleNamespace(
                    relative_path="doc-reader",
                    manifest=SimpleNamespace(
                        name="doc_reader",
                        description="Summarize documentation.",
                        entrypoint="SKILL.md",
                        permissions=("workspace:read",),
                    ),
                    manifest_digest="abc123",
                ),
            ),
        ),
    )
    assert skill_rows(inventory) == ("doc_reader (doc-reader) — Summarize documentation.",)


def test_mcp_groups_groups_by_namespace_and_sorts_names() -> None:
    entries = cast(
        tuple[CatalogEntryLike, ...],
        (
            SimpleNamespace(namespace="tools", qualified_name="mcp__srv__tools__beta"),
            SimpleNamespace(namespace="prompts", qualified_name="mcp__srv__prompts__greet"),
            SimpleNamespace(namespace="tools", qualified_name="mcp__srv__tools__alpha"),
        ),
    )
    assert mcp_groups(entries) == {
        "prompts": ("mcp__srv__prompts__greet",),
        "tools": ("mcp__srv__tools__alpha", "mcp__srv__tools__beta"),
    }
