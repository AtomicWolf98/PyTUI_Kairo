"""Structural DTO Protocols for workspace reads.

The real workspace DTOs (``WorkspaceTree``/``ChangedFiles``/``WorkspaceDiff``/
``WorkspacePreview``/``WorkspaceState``/``WorkspaceBookmark``) live in
``kairo_kernel.services.workspaces`` — an AST-forbidden module for the TUI
(``test_boundaries.py``). The TUI reads them structurally through these
attribute-compatible Protocols plus ``cast(...)``, per the Workbench plan
(``docs/superpowers/plans/2026-08-08-tui-workbench.md`` §Architecture).

The skill DTOs (``SkillInventory``/``SkillPackage``/``SkillManifest``) and MCP
DTOs (``CatalogEntry``/``McpCatalog``) live in ``kairo_kernel.skills`` and
``kairo_kernel.mcp`` — equally boundary-forbidden — and follow the same
structural pattern for the Extensions page.
"""

from __future__ import annotations

from typing import Protocol


class WorkspaceEntryLike(Protocol):
    name: str
    relative_path: str
    is_directory: bool
    size_bytes: int


class WorkspaceTreeLike(Protocol):
    root: str
    revision: int
    relative_path: str
    entries: tuple[WorkspaceEntryLike, ...]
    truncated: bool


class WorkspacePreviewLike(Protocol):
    root: str
    revision: int
    relative_path: str
    is_directory: bool
    size_bytes: int
    text: str
    children: tuple[str, ...]
    truncated: bool


class ChangedFileLike(Protocol):
    relative_path: str
    status: str


class ChangedFilesLike(Protocol):
    root: str
    revision: int
    is_git_repository: bool
    files: tuple[ChangedFileLike, ...]


class WorkspaceDiffLike(Protocol):
    root: str
    revision: int
    relative_path: str
    status: str
    unified_diff: str
    truncated: bool


class WorkspaceBookmarkLike(Protocol):
    name: str
    path: str


class WorkspaceStateLike(Protocol):
    root: str
    revision: int
    bookmarks: tuple[WorkspaceBookmarkLike, ...]


class SkillManifestLike(Protocol):
    name: str
    description: str
    entrypoint: str
    permissions: tuple[str, ...]


class SkillPackageLike(Protocol):
    relative_path: str
    manifest: SkillManifestLike
    manifest_digest: str


class SkillInventoryLike(Protocol):
    digest: str
    status: str
    packages: tuple[SkillPackageLike, ...]


class CatalogEntryLike(Protocol):
    namespace: str
    local_name: str
    qualified_name: str
    raw: dict[str, object]


class McpCatalogLike(Protocol):
    server_name: str
    tools: tuple[CatalogEntryLike, ...]
    resources: tuple[CatalogEntryLike, ...]
    prompts: tuple[CatalogEntryLike, ...]
