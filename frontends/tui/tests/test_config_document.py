"""Global config document load/save round-trips."""

from __future__ import annotations

import json
from pathlib import Path

from kairo_kernel.contracts.identifiers import ProfileId
from kairo_kernel.contracts.providers import ProviderProfile

from kairo_tui.config_document import ConfigDocument, ConfigDocumentAdapter, RoleMapping

PROFILE = ProviderProfile(
    ProfileId("p1"), "Model", "openai_responses", "gpt-5.2", "https://api.openai.com/v1", 32000, 1000, 0.2,
    secret_id="sk-ref-openai"
)


def test_missing_file_loads_empty_document(tmp_path: Path) -> None:
    adapter = ConfigDocumentAdapter(tmp_path / "config-v1.json")
    document = adapter.load()
    assert document.is_empty
    assert adapter.last_error is None


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    path = tmp_path / "config-v1.json"
    adapter = ConfigDocumentAdapter(path)
    document = ConfigDocument(
        profiles=(PROFILE,),
        roles=(RoleMapping("chat", PROFILE.profile_id),),
        mcp_servers=({"name": "files", "transport": "stdio", "command": "npx"},),
        default_profile_id=PROFILE.profile_id,
        theme="dark",
        keybindings=(("ctrl+k", "command_palette"),),
        recent_workspaces=(str(tmp_path),),
    )
    adapter.save(document)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    loaded = adapter.load()
    assert loaded == document
    assert not loaded.is_empty


def test_saved_file_is_utf8_lf_sorted(tmp_path: Path) -> None:
    path = tmp_path / "config-v1.json"
    ConfigDocumentAdapter(path).save(ConfigDocument())
    raw = path.read_bytes()
    assert b"\r\n" not in raw  # LF-only
    decoded = json.loads(raw.decode("utf-8"))
    assert decoded["version"] == 1


def test_corrupt_file_loads_empty_with_error(tmp_path: Path) -> None:
    path = tmp_path / "config-v1.json"
    path.write_text("{not json", encoding="utf-8")
    adapter = ConfigDocumentAdapter(path)
    assert adapter.load().is_empty
    assert adapter.last_error is not None


def test_unsupported_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config-v1.json"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    adapter = ConfigDocumentAdapter(path)
    assert adapter.load().is_empty
    assert adapter.last_error is not None


def test_malformed_keybinding_pairs_load_empty_with_error(tmp_path: Path) -> None:
    for keybindings in ([["ctrl+x"]], [["ctrl+x", "a", "b"]]):
        path = tmp_path / "config-v1.json"
        path.write_text(json.dumps({"version": 1, "keybindings": keybindings}), encoding="utf-8")
        adapter = ConfigDocumentAdapter(path)
        assert adapter.load().is_empty
        assert adapter.last_error is not None


def test_non_object_role_or_profile_entries_load_empty_with_error(tmp_path: Path) -> None:
    for payload in ({"roles": [["not", "a", "role"]]}, {"profiles": ["garbage"]}):
        path = tmp_path / "config-v1.json"
        path.write_text(json.dumps({"version": 1, **payload}), encoding="utf-8")
        adapter = ConfigDocumentAdapter(path)
        assert adapter.load().is_empty
        assert adapter.last_error is not None


def test_safe_mode_save_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "config-v1.json"
    adapter = ConfigDocumentAdapter(path, safe_mode=True)
    adapter.save(ConfigDocument(profiles=(PROFILE,)))
    assert not path.exists()
