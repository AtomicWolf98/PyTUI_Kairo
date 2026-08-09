"""Config path resolution (pure logic)."""

from __future__ import annotations

from pathlib import Path

from kairo_tui import paths


def test_default_config_path_uses_platformdirs_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "user_config_dir", lambda _name: str(tmp_path))
    resolved = paths.resolve_config_path(None)
    assert resolved == tmp_path / "config-v1.json"
    assert resolved.parent == paths.default_config_dir()


def test_config_override_wins(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "cfg.json"
    assert paths.resolve_config_path(override) == override


def test_override_expands_user_home(monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", str(Path("C:/fake-user").resolve()))
    resolved = paths.resolve_config_path("~/custom.json")
    assert str(resolved).endswith("custom.json")
