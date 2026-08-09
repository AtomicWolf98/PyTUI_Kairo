# Cutover + Release Gate — TDD Implementation Plan

Date: 2026-08-08 · Phase: 实施顺序 steps 5–6 (tui_plan.md) · Deliverable: legacy `--tui` compat jump, `agent/ui/` deletion, two 0.4.0a2 local-only Alpha wheels (kairo-kernel + kairo-tui), release gate evidence

## Goal

Finish the Kairo 0.4.0a2 TUI cutover and pass the release gate:

- **Compat entry (step 5)**: `kairo --tui` (and the auto-TTY Textual path) dispatch to the new `kairo-tui` package instead of the deleted `agent/ui/` Textual implementation. A missing `kairo_tui` install is a **hard error with an install hint** — never a silent fallback to plain. Plain and WebUI paths are untouched.
- **Delete the old Textual implementation**: remove `agent/ui/` after the compat entry lands and the TUI wheel builds; keep `agent/tui_widgets.py` for the plain frontend; retire the old-Textual tests that test deleted code.
- **Two-package release (step 6)**: rebuild both `0.4.0a2` Alpha wheels fresh — `kairo_kernel` from the root, `kairo_tui` from `frontends/tui/` — verify payload purity (kernel wheel only `kairo_kernel`, TUI wheel only `kairo_tui`, **no `agent/` in either**), isolated-install smoke, Twine, secret scan, AST boundaries, size matrix, Ruff/strict Mypy, `git diff --check`. Artifacts stay **local only**; nothing is published to PyPI; **no git commit/push anywhere** — the plan ends with an evidence report and a stop for user confirmation.
- Close the known release ledger items: (a) two timing flakes, (b) failing `tests/test_release_packaging.py`, (c) 21-entry command-palette overflow on 40-row screens, (d) README/CHANGELOG UTF-8/换行 hygiene + `git diff --check`, (e) `dist/` rebuilt to 0.4.0a2 for both packages, (f) CI OS×Python matrix with dual-wheel build.

End state (all green): kernel **318**, TUI **245** (236 + 9 new), root legacy suite **300** (337 − 43 deleted old-Textual + 5 compat + 1 packaging-net), Ruff clean, strict Mypy clean, `git diff --check` clean, `dist/` holds the two fresh 0.4.0a2 wheels, only `main` exists locally and remotely at the same commit.

## Architecture

```
legacy `kairo` CLI (kairo.py)
   ├── --web  ──────────────▶ agent.web            (unchanged)
   ├── --plain / TERM=dumb / non-TTY ───────────▶ agent.plain (unchanged)
   └── --tui / auto-TTY ────▶ run_new_tui(args)    (NEW compat jump)
                                └─ lazy import kairo_tui.app.KairoTuiApp.from_options(CliOptions)
                                   ├─ kairo_tui missing  → exit 2 + "pip install kairo-tui"
                                   └─ present            → run() the new Textual TUI
```

- The dispatch is a **pure translation function** with a lazy import, so the unit test fakes `sys.modules["kairo_tui.app"]` / `["kairo_tui.cli"]` and never boots the app.
- Wheels: `python -m build` (root → `kairo_kernel`), `python -m build frontends/tui --outdir dist` (→ `kairo_tui`) — both land in the single root `dist/`.
- Release checks: `tools/release_check.py` validates the two pyprojects derive versions from `kairo_kernel._version` / `kairo_tui._version` and that each wheel payload is **namespace-only** (no `agent/`, `tools/`, `web/`, `tests/`).
- CI: one OS×Python matrix job runs kernel + TUI suites + Ruff + Mypy; one wheel job builds both wheels, Twine-checks, runs wheel-content tests (kernel + TUI) and `pip_audit`.

## Tech Stack

- `kairo-kernel` public surface only from the TUI (AST boundary enforced by `frontends/tui/tests/test_boundaries.py`); kernel wheel built from root `pyproject.toml` (dynamic version `kairo_kernel._version.__version__`), TUI wheel from `frontends/tui/pyproject.toml` (`kairo_tui._version.__version__`, console script `kairo-tui = kairo_tui.cli:main`).
- Textual Pilot tests keep the established `asyncio.run(drive())` pattern (pytest-asyncio auto-mode rejects the nested `asyncio.run` inside `build_running_kernel`).
- `build>=1.2` and `twine>=6` are already root `[project.optional-dependencies].dev`; the TUI dev extra has `build` only — Twine runs from the root venv.
- Line endings: repo canonical form is LF (`core.autocrlf=true` on this Windows host; committed blobs are LF). README.md/CHANGELOG.md currently carry CRLF in the working tree — that is what makes `git diff --check` report `README.md:1: trailing whitespace.` today.

## Global Constraints

1. **Only `main`** — no branches, no worktrees, no parallel copies. `git branch -a` must show only `main` (+ `origin/main`).
2. **No git mutations anywhere** — no `git commit`, `git push`, `git add` staging beyond the user's own final commit. The plan ends with "report and await user confirmation to commit". Read-only `git` verification is allowed.
3. **No PyPI publish** — wheels are built, verified, and left in `dist/` only.
4. **Plain/WebUI unchanged** — `run_plain`, `agent.web`, the WebUI config block, and `agent/tui_widgets.py` are not modified (except removing now-dead imports if the cutover requires it).
5. **`agent/tui_widgets.py` is kept** — it serves the plain frontend; only `agent/ui/` (old Textual) is deleted.
6. **Every task ends green** — the exact suite counts in "Baseline & accounting" must hold after each task.
7. **`git diff --check` clean** and touched docs normalized to UTF-8 with a **single BOM** (exactly `ef bb bf` at byte 0) and LF line endings.
8. **Two wheels local-only** — `dist/` holds `kairo_kernel-0.4.0a2-py3-none-any.whl` and `kairo_tui-0.4.0a2-py3-none-any.whl` (sdists optional), no other versions.
9. **Kernel untouched by TUI tasks** — `tests/kernel` stays at 318; kernel changes are only additive CI/docs (Task 7/8 touch no kernel source).

## Baseline & accounting (verified 2026-08-09 on the real tree)

| Suite | Baseline | Deletions | Additions | Final |
|---|---|---|---|---|
| `pytest tests/kernel` | 318 | — | — | **318** |
| `pytest frontends/tui/tests` | 236 | — | T4 +5 · T6 +2 · T9 +2 | **245** |
| `pytest tests` (root, incl. kernel) | 655* | T2 −43 | T1 +5 · T3 +1 | **619** |
| of which legacy non-kernel | 337 | −43 (old-Textual) | +6 | **300** |

\* Root `pytest tests` currently **fails** on `tests/test_release_packaging.py` (pre-existing, fixed in T3). The 655 = 337 legacy + 318 kernel today.

Deletion math (T2): `tests/test_kairo_ui.py` 21 + `tests/test_settings_ui.py` 5 + `test_widgets.py` Textual-dependent 17 (`TestWindowsModifiedEnter` 3 + `TestComposer` 8 + `TestMessageBody` 6) = 43; the 4 plain `TestTUIWidgets` tests are kept.

## Evidence & decisions (verified against the real code)

- `kairo.py:200-212` — `should_use_textual` decides `--tui` / `--plain` / `TERM=dumb` / TTY. The old-Textual branch is `try: from agent.ui import KairoApp / except ImportError: run_plain(...)`. The `ImportError → plain` fallback must be removed for the Textual path (silent degradation is exactly what the spec forbids).
- `kairo.py` has **no positional argument** and **no `--safe-mode` / `--headless-smoke` flags** — those exist only on the new `kairo-tui` CLI (`frontends/tui/kairo_tui/cli.py`). The translation therefore maps only what the legacy CLI actually exposes: `--config` (default `"config.json"`), `--theme`, `--reduced-motion`, `--no-animation`. Workspace stays `None` → kairo-tui defaults to cwd.
- `kairo_tui.app.KairoTuiApp.from_options(CliOptions)` and the `CliOptions` dataclass fields (`workspace/config_path/theme/reduced_motion/safe_mode/headless_smoke`) are confirmed at `frontends/tui/kairo_tui/app.py:97-109` and `cli.py:10-20`.
- Legacy mode flags `--plan/--think/--auto/--authorization` mutate the OLD `config.json` before launch; the new TUI reads preferences from the versioned `config-v1.json` document which is **not migrated this phase** (tui_plan.md). They cannot be translated through `CliOptions` — the dispatch prints an explicit notice instead of silently dropping them.
- `main.py` (root) and the `kairo` shim both just call `kairo.main()` — they need **no changes**; the cutover lives entirely inside `kairo.py`.
- `git status` (2026-08-09): all phase-1–4 work is **uncommitted on `main`**; `git branch -a` shows only `main`/`origin/main`; local HEAD `db9ad4e` == `origin/main` HEAD. The final gate re-verifies this (read-only).
- `dist/` today: stale `kairo_agent-0.3.3-py3-none-any.whl`, `kairo_kernel-0.4.0a1-py3-none-any.whl`, `kairo_kernel-0.4.0a1.tar.gz`, plus a fresh `kairo_kernel-0.4.0a2-py3-none-any.whl`; `frontends/tui/dist/` has `kairo_tui-0.4.0a2` wheel+sdist (namespace-clean, verified). T3/T9 rebuild both 0.4.0a2 wheels into root `dist/` and delete the stale `kairo_agent-0.3.3` / `0.4.0a1` artifacts.
- `tools/release_check.py` is monolith-era: reads `agent/_version.py` (0.3.3), asserts `pyproject.toml` derives from `agent._version.__version__`, and validates packaged WebUI assets (`agent/web/static/`). The `kairo-agent` wheel with WebUI assets is **no longer produced**; the two 0.4.0a2 wheels carry no web assets → the WebUI checks are dropped. The kernel wheel's payload contract is already enforced by `tests/kernel/packaging/test_alpha_wheel.py`; the TUI wheel needs an equivalent test (`frontends/tui/tests/test_wheel_content.py`, T9).
- `tests/test_release_packaging.py` asserts `check_source_tree() == "0.3.3"` and builds a fake wheel with `agent/web/static/` content — both obsolete.
- Flakes: `test_exit_flow.py::test_exit_wait_completes_after_turn_finishes` pumps **20 bare `pilot.pause()`** frames while the FakeProvider completes only after a real `asyncio.wait_for(cancellation.wait(), timeout=0.2)` (`fakes.py:62-64`) — under full-suite load 20 frames elapse faster than 200 ms → `state.value != "stopped"` when asserted. `test_settings_screen.py::test_probe_surfaces_kernel_result` polls for the intermediate `"adapter"` substring with a 4 s budget — tighten to the final `"Probe unavailable"` status text with a larger budget. The codebase idiom for deterministic waits is the deadline-polling `_wait_for(pilot, predicate, polls, delay)` helper (`test_settings_screen.py:66-71`, `test_command_palette.py:27-31`).
- `screens/commands.py` composes the palette with a plain `Vertical`; 21 entries (10 TUI + 11 kernel) overflow the modal viewport — `test_command_palette.py:61-63` already has to press `#cmd-status` directly "because kernel entries can overflow the visible screen region". Fix: `VerticalScroll` + `max-height` CSS.
- README.md / CHANGELOG.md are UTF-8 **with BOM** and README has CRLF in the working tree; `git diff --check` currently reports `README.md:1: trailing whitespace.` (the CR artifact). frontends/tui/README.md is LF/no-BOM. Normalize the two docs to LF, keep the single BOM, then `git diff --check` is clean.
- `.github/workflows/ci.yml` today: kernel job on ubuntu × 3.11–3.14 only; `alpha-wheel` job pins `KAIRO_WHEEL=dist/kairo_kernel-0.4.0a2...`. Needs the OS matrix + dual-wheel build per 收口 §3.
- TUI suite command: `python -m pytest frontends/tui/tests` from the **repo root** (collects 236; the TUI pyproject's `pythonpath=["."]` alone cannot see `kairo_kernel` when run from `frontends/tui`). CI must run it from the root.
- `tests/kernel/contracts/test_contracts.py:383` lists `"agent.ui"` in a forbidden-strings check on kernel contracts — it stays valid after the deletion (contracts must not mention it).

---

## Task 1 — Compat entry: legacy `--tui` (and auto-TTY) dispatch to `kairo-tui`

The old Textual import is replaced by a translation function that lazily imports the new package. Both the explicit `--tui` flag and the auto-TTY path (the same `should_use_textual` decision logic, untouched) route through it, so deleting `agent/ui/` in Task 2 cannot silently degrade bare `kairo` in a terminal to plain.

**Files**
- `kairo.py` (root) — add `run_new_tui(args)`, rewire the `elif should_use_textual(...)` branch, update the `--tui` help text.
- tests: `tests/test_cutover_compat.py` (new, 5 tests).
- `main.py`, `kairo` shim — **no change** (they delegate to `kairo.main`).

**Interfaces — Consumes**: `kairo_tui.app.KairoTuiApp.from_options(CliOptions)`; `kairo_tui.cli.CliOptions` (lazy imports inside the function). **Produces**: `run_new_tui(args: argparse.Namespace) -> int`.

```python
def run_new_tui(args) -> int:
    """Compat jump: legacy Textual entry launches the kairo-tui package.

    Translates the legacy flags that map onto kairo-tui; a missing install is a
    hard error with an install hint — never a silent plain fallback.
    """
    try:
        from kairo_tui.app import KairoTuiApp
        from kairo_tui.cli import CliOptions
    except ImportError as exc:
        print(
            "[Kairo] kairo-tui 0.4.0a2 is not installed; run "
            f"`python -m pip install kairo-tui` (import error: {exc})",
            file=sys.stderr,
        )
        return 2
    if args.plan or args.think or args.auto or args.authorization:
        print(
            "[Kairo] Note: --plan/--think/--auto/--authorization are not translated "
            "to kairo-tui; use /mode inside the new TUI.",
            file=sys.stderr,
        )
    print("[Kairo] Launching kairo-tui 0.4.0a2 (legacy --tui entry).")
    options = CliOptions(
        workspace=None,  # legacy CLI has no positional; kairo-tui defaults to cwd
        config_path=None if args.config == "config.json" else args.config,
        theme=args.theme,
        reduced_motion=bool(args.reduced_motion or args.no_animation),
        safe_mode=False,      # not a legacy flag
        headless_smoke=False,  # not a legacy flag
    )
    KairoTuiApp.from_options(options).run()
    return 0
```

In `main()` replace the old-Textual branch:

```python
    elif should_use_textual(args, config):
        # Cutover (tui_plan.md step 5): the old agent/ui Textual implementation
        # is gone; --tui and the auto-TTY path jump to the kairo-tui package.
        raise SystemExit(run_new_tui(args))
```

Also update the flag help: `parser.add_argument("--tui", action="store_true", help="Launch the kairo-tui Textual interface even in non-TTY environments.")`.

**Steps**

1. Write `tests/test_cutover_compat.py` **first** (TDD). Patch strategy — the function's imports are lazy, so `unittest.mock.patch.dict("sys.modules", ...)` with small fake modules is enough; no app boot:

```python
import argparse
import sys
from types import SimpleNamespace
from unittest import mock

from kairo import run_new_tui


class _FakeCliOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeApp:
    def __init__(self):
        self.calls = []

    @classmethod
    def from_options(cls, options):
        app = cls()
        app.calls.append(options)
        return app

    def run(self):
        pass


def _install_fakes(monkeypatch):
    app_mod = SimpleNamespace(KairoTuiApp=_FakeApp)
    cli_mod = SimpleNamespace(CliOptions=_FakeCliOptions)
    monkeypatch.setitem(sys.modules, "kairo_tui", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "kairo_tui.app", app_mod)
    monkeypatch.setitem(sys.modules, "kairo_tui.cli", cli_mod)
    return cli_mod


def _args(**overrides):
    base = dict(config="config.json", theme=None, reduced_motion=False,
                no_animation=False, plan=False, think=False, auto=False, authorization=None)
    base.update(overrides)
    return argparse.Namespace(**base)
```

Five tests: (1) `test_tui_flag_dispatches_to_kairo_tui` — `run_new_tui(_args())` returns 0, `_FakeApp` recorded exactly one `CliOptions`, its `.run()` happened; (2) `test_translation_maps_explicit_config_theme_reduced_motion` — `config="cfg.json", theme="dark", reduced_motion=True` → recorded kwargs `{"workspace": None, "config_path": "cfg.json", "theme": "dark", "reduced_motion": True, "safe_mode": False, "headless_smoke": False}`; (3) `test_default_config_not_forwarded` — `config="config.json"` → `config_path is None`; (4) `test_no_animation_maps_to_reduced_motion` — `no_animation=True` → `reduced_motion is True`; (5) `test_missing_kairo_tui_is_hard_error_with_hint` — with the fakes **not** installed (delete `sys.modules["kairo_tui"]` etc. via `mock.patch.dict(sys.modules, ...)` minus keys, or patch `builtins.__import__` to raise `ImportError`), `run_new_tui(_args())` returns 2 and stderr contains `pip install kairo-tui`.

2. Implement `run_new_tui` and rewire `main()` exactly as above.
3. Run the compat tests, then the full root legacy suite minus the known-broken packaging test: `pytest tests/test_cutover_compat.py -q` → **5 passed**; `pytest tests -q -k "not release_packaging"` → **338 passed** (337 + 5, packaging excluded). `ruff check kairo.py`.

**Verification** — kernel 318 · TUI 236 · root legacy `pytest tests -k "not release_packaging"` = 338 · `ruff check kairo.py`.

---

## Task 2 — Delete `agent/ui/` and retire the old-Textual tests

The compat entry is the only production consumer of `agent.ui`; deleting the implementation now must leave the tree importable.

**Files**
- delete directory `agent/ui/` (`__init__.py`, `app.py`, `widgets.py`, `events.py`, `mascot.py`, `windows_keys.py`).
- delete `tests/test_kairo_ui.py` (21 tests), `tests/test_settings_ui.py` (5 tests) — they test deleted code.
- trim `tests/test_widgets.py` to the plain `TestTUIWidgets` block (4 tests: `test_is_wide_char`, `test_dock_text_truncates_without_breaking_wide_characters`, `test_select_menu_navigation`, `test_select_menu_wrap_around`): delete `TestWindowsModifiedEnter`, `TestComposer`, `TestMessageBody` and the `from agent.ui.widgets import ...` / `from agent.ui.windows_keys import ...` imports (lines 19–25); keep `from agent.tui_widgets import (...)`.
- keep `agent/tui_widgets.py`, `tests/kernel/contracts/test_contracts.py` (its `"agent.ui"` forbidden string stays valid), `tests/kernel/security/test_architecture.py`.
- tests: `tests/test_widgets.py` (trimmed), plus a new boundary guard `frontends/tui/tests/test_boundaries.py::test_legacy_agent_ui_deleted` — assert the repo no longer contains `agent/ui`:

```python
def test_legacy_agent_ui_deleted() -> None:
    agent_ui = PACKAGE.parents[2] / "agent" / "ui"
    assert not agent_ui.exists()
```

**Steps**

1. `grep -rn "agent\.ui" --include="*.py" .` → expect only `tests/kernel/contracts/test_contracts.py` (forbidden-string) plus hits inside `agent/ui/` itself. `kairo.py` must be clean (Task 1 removed the import).
2. Delete the directory and the two test files; trim `tests/test_widgets.py`.
3. Add the boundary guard to `test_boundaries.py` (TDD: write first, it fails before the deletion and passes after).
4. Run: `pytest tests/test_widgets.py tests/test_cutover_compat.py -q` → **9 passed** (4 + 5); `pytest tests -q -k "not release_packaging"` → **295 passed** (338 − 43); `pytest frontends/tui/tests -q` → **236**; `pytest tests/kernel -q` → **318**; `ruff check kairo.py agent tests` (agent now has no `ui/`).

**Verification** — kernel 318 · TUI 236 · root legacy (excl. packaging) = 295 · grep shows no `agent.ui` import anywhere.

---

## Task 3 — `tools/release_check.py` + `tests/test_release_packaging.py` to the two-package reality

The monolith-era checks (agent version, WebUI assets) describe a wheel that is no longer produced. Rewrite both to validate the two 0.4.0a2 packages and namespace-pure wheel payloads.

**Files**
- `tools/release_check.py` (rewrite, drop WebUI/`agent/_version.py`).
- `tests/test_release_packaging.py` (rewrite; keep the installer test).
- tests: same file (4 tests).

**Interfaces — Produces**

```python
"""Validate Kairo 0.4.0a2 release metadata and wheel payloads (kernel + TUI)."""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "0.4.0a2"

# distribution name -> (version-module source, pyproject, expected wheel prefix)
PACKAGES = {
    "kairo-kernel": (ROOT / "kairo_kernel" / "_version.py",
                     ROOT / "pyproject.toml",
                     "kairo_kernel-0.4.0a2-py3-none-any.whl"),
    "kairo-tui": (ROOT / "frontends" / "tui" / "kairo_tui" / "_version.py",
                  ROOT / "frontends" / "tui" / "pyproject.toml",
                  "kairo_tui-0.4.0a2-py3-none-any.whl"),
}


def source_version() -> str:            # kernel
    return _module_version(PACKAGES["kairo-kernel"][0])


def tui_version() -> str:              # TUI
    return _module_version(PACKAGES["kairo-tui"][0])


def _module_version(path: Path) -> str:
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                      path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise RuntimeError(f"{path} does not define __version__")
    return match.group(1)


def check_source_tree() -> str:
    versions = {name: _module_version(version) for name, (version, _, _) in PACKAGES.items()}
    if set(versions.values()) != {RELEASE}:
        raise RuntimeError(f"expected version {RELEASE}; got {versions}")
    expected_attr = {
        "kairo-kernel": "kairo_kernel._version.__version__",
        "kairo-tui": "kairo_tui._version.__version__",
    }
    for name, (_, pyproject, _) in PACKAGES.items():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        dynamic = project["project"].get("dynamic", [])
        attr = project["tool"]["setuptools"]["dynamic"]["version"]["attr"]
        if "version" not in dynamic or attr != expected_attr[name]:
            raise RuntimeError(f"{name} pyproject must derive its version from {expected_attr[name]}")
    return RELEASE


def check_wheel(wheel: Path, expected_version: str, namespace: str) -> None:
    """The wheel must carry ONLY ``namespace/`` + its own ``.dist-info/``."""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        dist_infos = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(dist_infos) != 1:
            raise RuntimeError(f"{wheel.name} has an unexpected METADATA layout")
        metadata = Parser().parsestr(archive.read(dist_infos[0]).decode("utf-8"))
        if metadata.get("Version") != expected_version:
            raise RuntimeError(
                f"{wheel.name} metadata version {metadata.get('Version')!r} does not match {expected_version!r}"
            )
        allowed = (f"{namespace}/", f"{namespace}-{expected_version}.dist-info/")
        offenders = sorted(name for name in names if not name.startswith(allowed))
        if offenders:
            raise RuntimeError(f"{wheel.name} contains unexpected entries: {', '.join(offenders)}")
        for forbidden in ("agent/", "tools/", "web/", "tests/"):
            if any(name.startswith(forbidden) for name in names):
                raise RuntimeError(f"{wheel.name} must not ship legacy {forbidden.strip('/')} content")
```

`main(argv)` — keep the `--wheel PATH` flag, **repeatable** (`action="append"`); derive each wheel's namespace from its basename (`kairo_kernel-...whl` → `kairo_kernel`); exit 1 with `release-check: <error>` on failure; success prints `release-check: Kairo 0.4.0a2 (kairo-kernel + kairo-tui) sources and wheel payloads are consistent`.

**Steps (TDD)**

1. Write the new `tests/test_release_packaging.py` first (4 tests):
   - `test_source_versions_are_0_4_0a2` — `check_source_tree() == "0.4.0a2"`, `source_version() == "0.4.0a2"`, `tui_version() == "0.4.0a2"`.
   - `test_wheel_validator_rejects_legacy_agent_content` — build a fake `kairo_tui-0.4.0a2-py3-none-any.whl` in a temp dir containing `kairo_tui-0.4.0a2.dist-info/METADATA` (Version 0.4.0a2) **plus** `agent/ui/app.py` → `assertRaisesRegex(RuntimeError, "unexpected entries")` via `check_wheel(wheel, "0.4.0a2", "kairo_tui")`.
   - `test_wheel_validator_rejects_wrong_version` — METADATA Version `0.4.0a1` → `assertRaisesRegex(RuntimeError, "does not match")`.
   - `test_installer_is_scoped_to_owned_installation` — **carry over unchanged** from the old file (install.bat manifest assertions).
2. Rewrite `tools/release_check.py` per the interface above.
3. Run: `pytest tests/test_release_packaging.py -q` → **4 passed**; `pytest tests -q` → **619 passed** (root suite green for the first time this phase — includes kernel 318).

**Verification** — kernel 318 · TUI 236 · root `pytest tests` = 619 · `python tools/release_check.py --wheel dist/kairo_kernel-0.4.0a2-py3-none-any.whl --wheel frontends/tui/dist/kairo_tui-0.4.0a2-py3-none-any.whl` succeeds.

---

## Task 4 — Size matrix tests: 80×24 / 100×30 / 140×40 / 200×50

The spec mandates the exact four sizes; `test_app_layout.py` covers nearby sizes but not the official matrix, and no test asserts "no crash + draft preserved" across the four canonical dimensions.

**Files**
- `frontends/tui/tests/test_size_matrix.py` (new, 5 tests).
- tests only — no production code change expected; if a size crashes, fix the layout in `kairo_tui/app.py` / `layout.py` (breakpoint logic verified already at `layout.py:15-23`).

**Steps (TDD)**

```python
"""Official size matrix (tui_plan.md): 80x24, 100x30, 140x40, 200x50."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairo_tui.app import KairoTuiApp
from kairo_tui.bootstrap import BootstrapOptions, build_running_kernel
from kairo_tui.keyring_store import SecretStore
from kairo_tui.store import DraftAction, PageAction, PageId

SIZES = [(80, 24, "overlay"), (100, 30, "narrow"), (140, 40, "full"), (200, 50, "full")]


@pytest.fixture
def matrix_app_factory(workspace: Path):
    def make(*, size: tuple[int, int]) -> KairoTuiApp:
        bootstrap = build_running_kernel(
            BootstrapOptions(workspace_root=str(workspace),
                             config_path=workspace.parent / "config-v1.json"),
            secret_store=SecretStore(None),
        )
        return KairoTuiApp(bootstrap)
    return make


@pytest.mark.parametrize("width,height,breakpoint", SIZES)
def test_layout_class_per_size(matrix_app_factory, width, height, breakpoint) -> None:
    app = matrix_app_factory(size=(width, height))

    async def drive() -> None:
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            assert app._breakpoint.value == breakpoint
            # Every page mounts without crashing at this size.
            for page in (PageId.CHAT, PageId.SESSIONS, PageId.WORKSPACE, PageId.MEMORY,
                         PageId.EXTENSIONS, PageId.SETTINGS, PageId.DOCTOR):
                app.store.dispatch(PageAction(page))
                await pilot.pause()
            assert app.query_one("#topbar") is not None
            assert app.query_one("#composer") is not None

    asyncio.run(drive())


def test_composer_draft_preserved_across_matrix(matrix_app_factory) -> None:
    app = matrix_app_factory(size=(200, 50))

    async def drive() -> None:
        async with app.run_test(size=(200, 50)) as pilot:
            await pilot.pause()
            app.store.dispatch(DraftAction("matrix draft"))
            for width, height, _ in SIZES:
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                assert app.store.state.draft == "matrix draft"

    asyncio.run(drive())
```

Run: `pytest frontends/tui/tests/test_size_matrix.py -q` → **5 passed** (4 parametrized + 1).

**Verification** — TUI 236 → **241** · kernel 318.

---

## Task 5 — Deterministic exit-flow and probe tests (ledger item a)

Root cause (verified): `test_exit_wait_completes_after_turn_finishes` pumps 20 bare `pilot.pause()` frames while the FakeProvider's turn completes after a real 0.2 s `asyncio.wait_for` — under full-suite load the frame pump races the clock. The codebase idiom is deadline-polling with `pilot.pause(0.05)` (e.g. `test_settings_screen.py:_wait_for`). Same hardening for the other bare-pause loops in the file; for the probe test, wait on the **final** status text with a larger budget.

**Files**
- `frontends/tui/tests/test_exit_flow.py` (hardened waits; 7 tests, count unchanged).
- `frontends/tui/tests/test_settings_screen.py::test_probe_surfaces_kernel_result` (hardened wait; count unchanged).

**Steps**

1. Add a module-level helper to `test_exit_flow.py`:

```python
async def _wait_for(pilot, predicate, *, polls: int = 100, delay: float = 0.05) -> None:
    for _ in range(polls):
        await pilot.pause(delay)
        if predicate():
            return
    raise AssertionError("condition not reached in time")
```

2. Replace the bare-pause loops (test-only, same assertions):
   - `test_exit_wait_completes_after_turn_finishes`: `for _ in range(20): await pilot.pause(); if ...: break` → `await _wait_for(pilot, lambda: app.kernel.state.value == "stopped")`; keep the final `assert ... == "stopped"`.
   - `test_exit_stop_all_cancels_and_exits`: after `pilot.click("#exit-stop")` → `await _wait_for(pilot, lambda: app.kernel.state.value == "stopped")`; keep `report.value == "stopped"`.
   - `test_esc_cancels_foreground_turn_only`: the two active-turn wait and the CANCELLED wait → `_wait_for(pilot, lambda: len(app.store.state.active_turns) == 2)` and `_wait_for(pilot, lambda: app.store.state.turn_status.get(str(turn_a.turn_id)) == TurnStatus.CANCELLED.value)`.
   - `test_exit_back_keeps_app_running`: `await _wait_for(pilot, lambda: not isinstance(app.screen, ExitWithTurnsModal))` before asserting `state.value == "running"`.
3. `test_probe_surfaces_kernel_result`: change the wait to the final status text with a bigger budget and keep both assertions:

```python
            await _wait_for(pilot, lambda: "Probe unavailable" in _status_text(app),
                            polls=200, delay=0.05)
            assert "not available" in _status_text(app).casefold()
```

4. Run each file 5× in sequence under load to prove determinism: `for i in 1 2 3 4 5; do pytest frontends/tui/tests/test_exit_flow.py -q || break; done` → 7 passed each time; same loop for the settings file (21 passed).

**Verification** — TUI 241 · kernel 318 · both files green across 5 consecutive runs.

---

## Task 6 — Palette scroll: 21 entries must fit 40-row screens (ledger item c)

**Files**
- `frontends/tui/kairo_tui/screens/commands.py` — `Vertical` → `VerticalScroll` + modal CSS.
- tests: `frontends/tui/tests/test_command_palette.py` (extend, +2 tests).

**Interfaces — Produces**

```python
from textual.containers import VerticalScroll
...
class CommandPaletteScreen(ModalScreen[None]):
    """List the merged command registry; run the selected one and close the palette."""

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; }
    CommandPaletteScreen #palette { width: 72; max-height: 80%;
                                    border: round $primary; background: $surface;
                                    padding: 1 2; }
    CommandPaletteScreen #palette Button { width: 100%; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="palette"):
            yield Label("[b]Commands[/b]")
            for entry in self._entries:
                yield Button(
                    f"{entry.name} — {entry.help or entry.summary}",
                    id=f"cmd-{entry.name.removeprefix('/')}",
                )
```

(`VerticalScroll` keeps the `id="palette"`; the two existing tests query `#cmd-*` buttons on the screen, which is unaffected.)

**Steps (TDD)**

1. Write the two tests first in `test_command_palette.py` (reuse its `_app` helper and the `asyncio.run(drive())` pattern):
   - `test_palette_is_scrollable_when_overflowing` — at `(140, 40)` open the palette (`app.action_command_palette()`), `palette = app.screen.query_one("#palette", VerticalScroll)`, `await pilot.pause()`, assert `palette.max_scroll_y >= 1` (the 21 entries overflow).
   - `test_palette_scroll_reaches_last_kernel_command` — at `(80, 24)` open the palette, `await palette.scroll_end()`, `await pilot.pause()`, assert `palette.scroll_y == palette.max_scroll_y`; then `app.screen.query_one("#cmd-status", Button).press()` and `await _wait_for(pilot, lambda: len(app.screen_stack) == 1)` (executes and dismisses).
2. Implement the `VerticalScroll` + CSS change.
3. Run: `pytest frontends/tui/tests/test_command_palette.py -q` → **5 passed** (3 existing + 2 new); full TUI suite → **243**.

**Verification** — TUI 241 → **243** · kernel 318.

---

## Task 7 — README/CHANGELOG to 0.4.0a2 + UTF-8/换行 hygiene (ledger item d)

**Files**
- `README.md` (root) — version line → `0.4.0a2`; rewrite the intro for the two-package reality: `kairo-kernel` (frontend-neutral kernel) + `kairo-tui` (Textual frontend); document `kairo-tui [WORKSPACE]` with `--config/--theme/--reduced-motion/--safe-mode/--headless-smoke`; document the compat jump (`kairo --tui` launches kairo-tui 0.4.0a2; missing install shows an install hint); state that legacy `config.json`/sessions are not migrated this phase; keep the bilingual layout.
- `frontends/tui/README.md` — add a version/compat line: kairo-tui 0.4.0a2, launched directly or via legacy `kairo --tui`.
- `CHANGELOG.md` — extend the existing `[0.4.0a2]` section with a `### TUI / 发行` block: new `kairo-tui` package + CLI; `kairo --tui` compat entry; old `agent/ui/` Textual implementation removed (plain `agent.tui_widgets.py` kept); size matrix / Pilot / headless smoke test surface; wheels split (kernel-only and TUI-only payloads); both wheels local-only.
- tests: none (docs-only), but the hygiene verification is itself the gate.

**Steps**

1. Update the three docs. Keep the existing bilingual convention (English line + Chinese line in root README/CHANGELOG).
2. Normalize encodings/endings on the touched files — keep exactly **one** BOM (`ef bb bf` at byte 0), convert CRLF → LF:

```bash
# convert CRLF -> LF in place, preserving the existing single BOM:
.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
for name in ("README.md", "CHANGELOG.md", "frontends/tui/README.md"):
    path = Path(name)
    data = path.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(data)
PY
```

3. Verify: `git diff --check` → **no output** (currently reports `README.md:1: trailing whitespace.`); BOM check `head -c 3 README.md | xxd` → `efbbbf` exactly once, no stray BOM in the middle (grep for `efbbbf` at non-zero offsets); `file README.md CHANGELOG.md` shows `Unicode text, UTF-8 (with BOM) text` with no CRLF mention.

**Verification** — `git diff --check` clean · both docs single-BOM UTF-8 + LF · TUI 243 · kernel 318.

---

## Task 8 — CI matrix: OS × Python 3.11–3.14, dual-wheel build (收口 §3)

The workflow is defined here; actual macos/ubuntu/py3.14 validation happens in the user's CI (cannot run on this Windows host — see escalations).

**Files**
- `.github/workflows/ci.yml` (rewrite).

**Steps**

```yaml
name: Kairo CI (kernel + TUI)

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test:
    name: ${{ matrix.os }} / Python ${{ matrix.python-version }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python-version: ["3.11", "3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e ".[dev]"          # root: kairo-kernel editable
      - run: python -m pip install -e "frontends/tui[dev]"  # satisfies kairo-kernel==0.4.0a2 from the editable install
      - run: python -m pytest tests/kernel --cov=kairo_kernel --cov-report=term-missing
      - run: python -m pytest frontends/tui/tests
      - run: python -m ruff check kairo_kernel tests/kernel
      - name: Ruff (TUI)
        run: python -m ruff check kairo_tui tests
        working-directory: frontends/tui
      - run: python -m mypy kairo_kernel
      - name: Mypy (TUI, strict)
        run: python -m mypy kairo_tui
        working-directory: frontends/tui   # picks up frontends/tui/pyproject.toml [tool.mypy]

  alpha-wheel:
    name: Alpha wheels (kernel + TUI)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e ".[dev]"
      - run: python -m build                                        # kernel -> dist/
      - run: python -m build frontends/tui --outdir dist            # TUI -> dist/
      - run: python -m twine check dist/*.whl
      - run: KAIRO_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl python -m pytest tests/kernel/packaging
      - run: KAIRO_TUI_WHEEL=dist/kairo_tui-0.4.0a2-py3-none-any.whl \
             KAIRO_KERNEL_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl \
             python -m pytest frontends/tui/tests/test_wheel_content.py
      - run: python -m pip_audit .
```

Notes to document in the PR/commit body: the TUI mypy/ruff steps run with `working-directory: frontends/tui` so they pick up the TUI's own stricter `[tool.mypy]` (`check_untyped_defs`, `no_implicit_optional`, `warn_unused_ignores`) and ruff config; the TUI pytest runs from the repo root because `frontends/tui/pyproject.toml`'s `pythonpath=["."]` cannot see `kairo_kernel` when invoked from inside `frontends/tui`.

**Verification** — workflow parses (`python -m yaml` or actionlint if available); no local run possible — validated by the user's CI. TUI 243 · kernel 318 unchanged.

---

## Task 9 — Release gate verification: build, twine, isolated install, secret scan, full suites (ledger items b/e; step 6)

Local-only gate. **No commits.** Read-only git checks only.

**Files**
- `frontends/tui/tests/test_wheel_content.py` (new, +2 tests — the TUI counterpart of `tests/kernel/packaging/test_alpha_wheel.py`).
- artifacts: root `dist/` rebuilt; stale artifacts deleted.

**Steps**

1. **TDD first** — write `frontends/tui/tests/test_wheel_content.py`:

```python
"""TUI wheel payload + isolated-install smoke (mirrors tests/kernel/packaging/test_alpha_wheel.py)."""
from __future__ import annotations

import email
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

from kairo_tui._version import __version__

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = "kairo_tui-0.4.0a2-py3-none-any.whl"


def wheel_path() -> Path:
    configured = os.environ.get("KAIRO_TUI_WHEEL", "").strip()
    wheel = Path(configured) if configured else ROOT / "dist" / EXPECTED
    if not wheel.is_absolute():
        wheel = ROOT / wheel
    if not wheel.is_file():
        pytest.fail(f"Build the TUI alpha wheel before running wheel smoke tests: {wheel}")
    return wheel.resolve()


def kernel_wheel_path() -> Path:
    configured = os.environ.get("KAIRO_KERNEL_WHEEL", "").strip()
    wheel = Path(configured) if configured else ROOT / "dist" / "kairo_kernel-0.4.0a2-py3-none-any.whl"
    if not wheel.is_absolute():
        wheel = ROOT / wheel
    if not wheel.is_file():
        pytest.fail(f"Build the kernel alpha wheel before running wheel smoke tests: {wheel}")
    return wheel.resolve()


def test_tui_wheel_contains_only_tui_and_distribution_metadata() -> None:
    with zipfile.ZipFile(wheel_path()) as archive:
        names = archive.namelist()
    assert "kairo_tui/py.typed" in names
    assert "kairo_tui/_version.py" in names
    assert all(name.startswith(("kairo_tui/", "kairo_tui-0.4.0a2.dist-info/")) for name in names)
    assert not any(name.startswith(("agent/", "tools/", "tests/", "web/")) for name in names)
    assert not any(name.startswith("kairo.py") for name in names)
    assert any(name.endswith("entry_points.txt") for name in names)  # kairo-tui console script


def test_tui_wheel_installs_isolated_and_smokes() -> None:
    environment = Path(os.environ.get("KAIRO_SMOKE_TMP", "")) if os.environ.get("KAIRO_SMOKE_TMP") else None
    tmp = __import__("tempfile").TemporaryDirectory()
    venv_dir = Path(tmp.name) / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall",
         str(kernel_wheel_path()), str(wheel_path())],
        check=True, capture_output=True, text=True,
    )
    working = Path(tmp.name) / "outside-source"
    working.mkdir()
    child = dict(os.environ)
    child.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(python), "-m", "kairo_tui", "--headless-smoke"],
        cwd=working, env=child, check=True, capture_output=True, text=True,
    )
    assert "KAIRO_TUI_SMOKE_OK" in completed.stdout
```

2. Delete stale artifacts (documented, reversible — they are rebuildable): `rm dist/kairo_agent-0.3.3-py3-none-any.whl dist/kairo_kernel-0.4.0a1-py3-none-any.whl dist/kairo_kernel-0.4.0a1.tar.gz frontends/tui/dist/kairo_tui-0.4.0a2-py3-none-any.whl frontends/tui/dist/kairo_tui-0.4.0a2.tar.gz`.
3. Build fresh: `python -m build` (root → `dist/kairo_kernel-0.4.0a2-py3-none-any.whl`) and `python -m build frontends/tui --outdir dist` (→ `dist/kairo_tui-0.4.0a2-py3-none-any.whl`).
4. Verify content contract via release_check + twine + the two wheel suites:
   - `python tools/release_check.py --wheel dist/kairo_kernel-0.4.0a2-py3-none-any.whl --wheel dist/kairo_tui-0.4.0a2-py3-none-any.whl` → success line.
   - `python -m twine check dist/*.whl` → `Checking dist/kairo_kernel-...: PASSED` ×2.
   - `KAIRO_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl python -m pytest tests/kernel/packaging -q` → **5 passed**.
   - `KAIRO_TUI_WHEEL=dist/kairo_tui-0.4.0a2-py3-none-any.whl KAIRO_KERNEL_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl python -m pytest frontends/tui/tests/test_wheel_content.py -q` → **2 passed** (the isolated install smoke boots the real app from the wheels, outside the source tree).
5. Secret scan: `python -m pytest frontends/tui/tests/test_secret_scan.py -q` → **3 passed** (document/repr/export never leak full key material); plus the AST boundaries proving no legacy imports: `python -m pytest frontends/tui/tests/test_boundaries.py tests/kernel/security/test_architecture.py -q` → green.
6. Full suites: `python -m pytest tests/kernel -q` → **318** · `python -m pytest frontends/tui/tests -q` → **245** · `python -m pytest tests -q` → **619**.
7. Lint/type: `python -m ruff check kairo_kernel tests/kernel` · `python -m ruff check kairo_tui tests` (cwd `frontends/tui`) · `python -m mypy kairo_kernel` · `python -m mypy kairo_tui` (cwd `frontends/tui`) — all clean.
8. Read-only git gate (no mutations): `git diff --check` → empty · `git branch -a` → only `main`/`origin/main` · `git rev-parse HEAD` vs `git ls-remote origin main` → identical commit · `git status --short` shows only the expected working-tree changes (no new branches, no staged commits).
9. **STOP** — do not commit, push, or tag. Produce the evidence report (Task 10) and await the user's confirmation to commit.

**Verification** — wheels exist in `dist/` (two, 0.4.0a2) · twine PASSED · wheel-content suites green · secret scan green · kernel 318 · TUI **245** · root `pytest tests` **619** · ruff/mypy clean · `git diff --check` clean · only `main`, local == remote HEAD.

---

## Task 10 — Final ledger update + release-gate evidence report

**Files**: none required — the evidence report is the implementing agent's final message (per existing SDD convention, a copy may be written to `.superpowers/sdd/cutover-release-gate-report.md` if the user wants a file).

**Steps**

1. Re-run the six-checks recap from Task 9 step 8 and confirm the wheel suite totals.
2. Produce the report with a per-gate evidence table (command → output tail → PASS/FAIL): compat dispatch, agent/ui deletion, size matrix, flakes (5× consecutive runs), palette scroll, docs hygiene, CI workflow (documented, pending user CI), wheel build + twine + isolated install, secret scan, AST boundaries, full suites, git state.
3. Update the plan's baseline table if any count drifted (should be kernel 318 / TUI 245 / root `pytest tests` 619).
4. **Await user confirmation to commit** — the plan ends here; the user approves the single `main` commit and push.

**Verification** — report delivered; all gate rows PASS; nothing committed.

---

## Escalated ambiguities

1. **`--config` semantics differ between CLIs.** Legacy `--config` defaults to `config.json` (old-format monolith config); kairo-tui's `--config` names the versioned `config-v1.json` global document. The dispatch forwards only an **explicitly provided** `--config` and drops the default; a forwarded legacy path that is not a v1 document is treated as absent by `ConfigDocumentAdapter` → the Setup page appears (config is not migrated this phase, per tui_plan.md). Confirm this is the desired behavior for users with an existing legacy `config.json`.
2. **Legacy mode flags have no kairo-tui equivalent.** `--plan/--think/--auto/--authorization` cannot be translated through `CliOptions` (kairo-tui reads authorization/Plan/Thinking from the versioned document). The dispatch prints a notice and ignores them. If honoring them at boot is required, a follow-up would extend `CliOptions`/`BootstrapOptions` with an initial `PreferencesPatch` — out of scope here.
3. **Bare `kairo` in a TTY routes to kairo-tui.** The spec names only `--tui`, but the old Textual import also served the auto-TTY path; once `agent/ui/` is deleted, that branch must go somewhere. The plan keeps the decision logic and swaps the target (bare-TTY → kairo-tui), rejecting the alternative (bare-TTY → plain) as a silent UX regression that contradicts the "helpful error, no silent fallback" rule. Confirm.
4. **Old-Textual test fate.** `tests/test_kairo_ui.py` (21), `tests/test_settings_ui.py` (5) and the 17 Textual-dependent tests in `tests/test_widgets.py` test deleted code and are removed; the 4 plain `agent.tui_widgets` tests in `test_widgets.py` are kept. Alternative (keep the tests, keep `agent/ui/`) was rejected — it contradicts step 5.
5. **WebUI asset checks dropped from `release_check.py`.** They validated the old `kairo-agent` wheel (0.3.3) which is no longer built; the two 0.4.0a2 wheels carry no web assets. If a legacy agent wheel remains a supported artifact, the WebUI checks belong in a separate legacy pipeline — flagging for confirmation.
6. **CI matrix validated by the user's CI, not locally.** This host is Windows; the macOS/Ubuntu × 3.11–3.14 legs of the new workflow and the strict-Mypy TUI step can only be exercised in GitHub Actions. The workflow and its config-resolution notes are fully specified in Task 8.
7. **`python -m kairo_tui --headless-smoke` runs with a real kernel** from the isolated wheel install (Task 9) — it creates `.kairo/` state under the temp cwd and requires the temp venv to see the runner's site-packages (`system_site_packages=True`, same as the existing kernel packaging test). No network access is needed (FakeProvider only).

## Final gate commands (one-stop, Task 9)

```bash
cd /c/Users/Admin/Desktop/project/pyTUI
# 1. build both wheels fresh into dist/
.venv/Scripts/python.exe -m build
.venv/Scripts/python.exe -m build frontends/tui --outdir dist
# 2. release checks
.venv/Scripts/python.exe tools/release_check.py --wheel dist/kairo_kernel-0.4.0a2-py3-none-any.whl --wheel dist/kairo_tui-0.4.0a2-py3-none-any.whl
.venv/Scripts/python.exe -m twine check dist/*.whl
# 3. wheel suites
KAIRO_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl .venv/Scripts/python.exe -m pytest tests/kernel/packaging -q
KAIRO_TUI_WHEEL=dist/kairo_tui-0.4.0a2-py3-none-any.whl KAIRO_KERNEL_WHEEL=dist/kairo_kernel-0.4.0a2-py3-none-any.whl .venv/Scripts/python.exe -m pytest frontends/tui/tests/test_wheel_content.py -q
# 4. full suites (expected: kernel 318 / TUI 245 / root pytest tests 619)
.venv/Scripts/python.exe -m pytest tests/kernel -q
.venv/Scripts/python.exe -m pytest frontends/tui/tests -q
.venv/Scripts/python.exe -m pytest tests -q
# 5. lint / type / secret / boundaries
.venv/Scripts/python.exe -m ruff check kairo_kernel tests/kernel
.venv/Scripts/python.exe -m ruff check frontends/tui/kairo_tui frontends/tui/tests
.venv/Scripts/python.exe -m mypy kairo_kernel
(cd frontends/tui && ../.venv/Scripts/python.exe -m mypy kairo_tui)
.venv/Scripts/python.exe -m pytest frontends/tui/tests/test_secret_scan.py frontends/tui/tests/test_boundaries.py tests/kernel/security/test_architecture.py -q
# 6. read-only git gate
git diff --check
git branch -a
git rev-parse HEAD && git ls-remote origin main
# 7. STOP — report evidence, await user confirmation to commit
```
