"""Cutover compat: legacy `kairo --tui` (and the auto-TTY path) dispatch to kairo-tui.

`run_new_tui` lazily imports `kairo_tui`, so `sys.modules` is faked here with
small stand-ins — no app boot.
"""

import argparse
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from kairo import run_new_tui


class _FakeCliOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeApp:
    instances = []

    def __init__(self):
        self.calls = []
        self.ran = False
        _FakeApp.instances.append(self)

    @classmethod
    def from_options(cls, options):
        app = cls()
        app.calls.append(options)
        return app

    def run(self):
        self.ran = True


@pytest.fixture(autouse=True)
def _reset_fake_apps():
    _FakeApp.instances = []


def _install_fakes(monkeypatch):
    app_mod = SimpleNamespace(KairoTuiApp=_FakeApp)
    cli_mod = SimpleNamespace(CliOptions=_FakeCliOptions)
    monkeypatch.setitem(sys.modules, "kairo_tui", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "kairo_tui.app", app_mod)
    monkeypatch.setitem(sys.modules, "kairo_tui.cli", cli_mod)
    return cli_mod


def _args(**overrides):
    base = {
        "config": "config.json",
        "theme": None,
        "reduced_motion": False,
        "no_animation": False,
        "plan": False,
        "think": False,
        "auto": False,
        "authorization": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_tui_flag_dispatches_to_kairo_tui(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert run_new_tui(_args()) == 0
    assert "Launching kairo-tui 0.4.0a2" in capsys.readouterr().out
    assert len(_FakeApp.instances) == 1
    app = _FakeApp.instances[0]
    assert len(app.calls) == 1
    assert isinstance(app.calls[0], _FakeCliOptions)
    assert app.ran is True


def test_translation_maps_explicit_config_theme_reduced_motion(monkeypatch):
    _install_fakes(monkeypatch)
    run_new_tui(_args(config="cfg.json", theme="dark", reduced_motion=True))
    options = _FakeApp.instances[0].calls[0]
    assert options.kwargs == {
        "workspace": None,
        "config_path": "cfg.json",
        "theme": "dark",
        "reduced_motion": True,
        "safe_mode": False,
        "headless_smoke": False,
    }


def test_default_config_not_forwarded(monkeypatch):
    _install_fakes(monkeypatch)
    run_new_tui(_args(config="config.json"))
    options = _FakeApp.instances[0].calls[0]
    assert options.kwargs["config_path"] is None


def test_no_animation_maps_to_reduced_motion(monkeypatch):
    _install_fakes(monkeypatch)
    run_new_tui(_args(no_animation=True))
    options = _FakeApp.instances[0].calls[0]
    assert options.kwargs["reduced_motion"] is True


def test_missing_kairo_tui_is_hard_error_with_hint(capsys):
    missing = {"kairo_tui": None, "kairo_tui.app": None, "kairo_tui.cli": None}
    with mock.patch.dict(sys.modules, missing):
        assert run_new_tui(_args()) == 2
    err = capsys.readouterr().err
    assert "pip install kairo-tui" in err
