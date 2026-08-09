"""Kernel bootstrap + role seeding (real kernel, tmp workspace)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.lifecycle import LifecycleState
from kairo_kernel.contracts.providers import ProviderProfile

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel, seed_role_mappings
from kairo_tui.config_document import ConfigDocument, RoleMapping
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import PageId


def _profile(profile_id: str = "p1") -> ProviderProfile:
    return ProviderProfile(
        ProfileId(profile_id), "Model", "openai_responses", "gpt-5.2",
        "https://api.openai.com/v1", 32000, 1000, 0.2,
    )


def _document_json(document: ConfigDocument) -> str:
    return json.dumps(document.to_dict())


def test_build_running_kernel_starts_in_tmp_workspace(workspace: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "config-v1.json"
    config_path.write_text('{"version": 1}', encoding="utf-8")
    result = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=config_path),
        secret_store=SecretStore(None),
    )
    assert result.kernel.state is LifecycleState.RUNNING
    assert result.store.state.page is PageId.SETUP  # empty document
    assert result.config_error is None


def test_build_running_kernel_seeds_roles_from_document(workspace: Path, tmp_path: Path) -> None:
    document = ConfigDocument(
        profiles=(_profile(),),
        roles=(RoleMapping("chat", _profile().profile_id),),
        default_profile_id=_profile().profile_id,
    )
    config_path = tmp_path / "config-v1.json"
    config_path.write_text(_document_json(document), encoding="utf-8")
    result = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=config_path),
        secret_store=SecretStore(None),
    )
    assert result.store.state.setup_complete is True
    assert result.store.state.page is PageId.CHAT
    resolved = None
    async def check() -> None:
        nonlocal resolved
        resolved = await result.kernel.providers.resolve(None, "chat")
    asyncio.run(check())
    assert resolved is not None and resolved.ok


def test_seed_role_mappings_uses_expected_revision(kernel_factory) -> None:
    kernel = kernel_factory(profiles=(_profile(),))

    async def exercise() -> None:
        await kernel.start()
        applied = await seed_role_mappings(
            kernel, ConfigDocument(roles=(RoleMapping("chat", _profile().profile_id),))
        )
        assert applied == ["chat"]
        await kernel.shutdown()

    asyncio.run(exercise())


def test_theme_option_applies_at_startup(workspace: Path, tmp_path: Path) -> None:
    """`--theme nord` bootstrap lands in the document and is applied to the
    running Textual app on mount ("nord" is a built-in Textual theme)."""
    config_path = tmp_path / "config-v1.json"
    config_path.write_text('{"version": 1}', encoding="utf-8")
    result = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=config_path, theme="nord"),
        secret_store=SecretStore(None),
    )
    assert result.store.state.document.theme == "nord"
    app = KairoTuiApp(result)

    async def drive() -> None:
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert app.theme == "nord"

    asyncio.run(drive())


def test_safe_mode_never_writes_config(workspace: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "config-v1.json"
    result = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=config_path, safe_mode=True),
        secret_store=SecretStore(None),
    )
    assert result.kernel.state is LifecycleState.RUNNING
    assert not config_path.exists()  # no persisted writes in safe mode


def test_build_running_kernel_trusts_skills_with_store_outside_workspace(
    workspace: Path, tmp_path: Path, monkeypatch
) -> None:
    """The booted kernel must be able to trust skills: the configured trust
    store must live outside the workspace (the kernel fail-closes on an
    in-workspace store) and ``kernel.skills.trust`` must be reachable."""
    skill = workspace / ".kairo" / "skills" / "echo"
    skill.mkdir(parents=True)
    (skill / "skill.json").write_text(
        json.dumps(
            {
                "name": "echo",
                "description": "echo description",
                "entrypoint": "SKILL.md",
                "permissions": ["workspace:read"],
            }
        ),
        encoding="utf-8",
    )
    (skill / "SKILL.md").write_text("# Echo", encoding="utf-8")

    # Point the platformdirs user-data dir at the test sandbox (repo idiom:
    # test_paths monkeypatches kairo_tui.paths.user_config_dir likewise).
    import kairo_tui.paths as paths_module

    monkeypatch.setattr(paths_module, "user_data_dir", lambda _name: str(tmp_path / "kairo-data"))

    config_path = tmp_path / "config-v1.json"
    config_path.write_text('{"version": 1}', encoding="utf-8")
    result = build_running_kernel(
        BootstrapOptions(workspace_root=str(workspace), config_path=config_path),
        secret_store=SecretStore(None),
    )
    assert result.kernel.state is LifecycleState.RUNNING

    trust_dir = paths_module.default_trust_dir()
    assert trust_dir.is_absolute()
    assert not trust_dir.is_relative_to(workspace)  # fail-closed store stays outside the workspace

    async def exercise() -> None:
        inventory = await result.kernel.skills.inspect()
        assert inventory.status == "untrusted"
        outcome = await result.kernel.skills.trust(inventory.digest)
        assert outcome.ok
        assert outcome.value is not None and outcome.value.status == "trusted"

    asyncio.run(exercise())
