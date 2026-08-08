from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairo_kernel.skills import SkillManifestError, SkillRegistry, SkillTrustStore, parse_manifest, snapshot_directory


def create_skill(workspace: Path, *, name: str = "echo", body: str = "# Echo") -> Path:
    root = workspace / ".kairo" / "skills" / name
    root.mkdir(parents=True)
    (root / "skill.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": f"{name} description",
                "entrypoint": "SKILL.md",
                "permissions": ["workspace:read"],
            }
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_skills_are_inert_until_exact_directory_digest_is_trusted(tmp_path):
    workspace = tmp_path / "workspace"
    create_skill(workspace)
    registry = SkillRegistry(workspace, ".kairo/skills", SkillTrustStore(tmp_path / "trust.json"))

    inspected = await registry.inspect()
    trusted = await registry.trust(inspected.digest)

    assert inspected.status == "untrusted"
    assert inspected.packages[0].manifest.name == "echo"
    assert trusted.status == "trusted"
    assert (await registry.active()) == trusted.packages
    assert trusted.packages[0].files[0][0] == "SKILL.md"


@pytest.mark.asyncio
async def test_stale_digest_is_rejected_without_activating_skill(tmp_path):
    workspace = tmp_path / "workspace"
    skill = create_skill(workspace)
    registry = SkillRegistry(workspace, ".kairo/skills", SkillTrustStore(tmp_path / "trust.json"))
    digest = (await registry.inspect()).digest
    (skill / "SKILL.md").write_text("changed", encoding="utf-8")

    with pytest.raises(SkillManifestError, match="changed after review"):
        await registry.trust(digest)

    assert await registry.active() == ()


@pytest.mark.asyncio
async def test_reload_unloads_changed_manifest_and_revoke_removes_trust(tmp_path):
    workspace = tmp_path / "workspace"
    skill = create_skill(workspace)
    store = SkillTrustStore(tmp_path / "trust.json")
    registry = SkillRegistry(workspace, ".kairo/skills", store)
    await registry.trust((await registry.inspect()).digest)
    (skill / "SKILL.md").write_text("changed", encoding="utf-8")

    changed = await registry.reload()
    revoked = await registry.revoke()

    assert changed.status == "changed"
    assert changed.packages == ()
    assert revoked
    assert store.trusted_digest(workspace) == ""


@pytest.mark.asyncio
async def test_reload_rechecks_snapshot_after_parse_to_close_toctou_window(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    skill = create_skill(workspace)
    registry = SkillRegistry(workspace, ".kairo/skills", SkillTrustStore(tmp_path / "trust.json"))
    await registry.trust((await registry.inspect()).digest)
    original = snapshot_directory
    calls = 0

    def mutate_between_snapshots(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            (skill / "SKILL.md").write_text("mutated during load", encoding="utf-8")
        return original(path)

    monkeypatch.setattr("kairo_kernel.skills.registry.snapshot_directory", mutate_between_snapshots)

    result = await registry.reload()

    assert result.status == "changed"
    assert await registry.active() == ()


def test_manifest_digest_covers_names_and_content_and_parser_rejects_traversal(tmp_path):
    root = create_skill(tmp_path / "workspace")
    first = snapshot_directory(root.parent).digest
    (root / "extra.txt").write_text("extra", encoding="utf-8")
    second = snapshot_directory(root.parent).digest
    invalid = json.dumps({"name": "x", "description": "x", "entrypoint": "../escape"}).encode()

    assert first != second
    with pytest.raises(SkillManifestError, match="unsafe entrypoint"):
        parse_manifest(invalid)


@pytest.mark.asyncio
async def test_trust_store_cannot_live_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    create_skill(workspace)
    registry = SkillRegistry(
        workspace,
        ".kairo/skills",
        SkillTrustStore(workspace / ".kairo" / "trust.json"),
    )

    with pytest.raises(SkillManifestError, match="outside"):
        await registry.trust((await registry.inspect()).digest)
