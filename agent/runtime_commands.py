"""0.2.3 command handlers for runtime config, health check, session management.

These handlers are invoked from :class:`CommandDispatcher` (both the plain
console path and the Textual modal layer). Plain-mode flows use
:mod:`agent.plain_io` for synchronous prompts; the Textual layer will be
wired in Phase 3 by reading the ``interactive`` flag and ``kind`` field on
the returned :class:`CommandResult` and swapping the plain prompt chain for
a modal.

Each handler returns a :class:`CommandResult` so the dispatcher stays a thin
router and there is no shared mutable state beyond the agent.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.commands import CommandResult
from agent.config import Config
from agent.config_editor import ConfigDraft, KEY_CLEAR
from agent.plain_io import (
    ask,
    ask_choice,
    ask_float,
    ask_int,
    banner,
    confirm,
    error,
    notice,
    select,
)
from agent.profile_resolver import describe_key_source, list_profiles, mask_key
from agent.provider_health import ProviderTestResult, test_connection


# ---- Shared helpers ------------------------------------------------------------


def _list_profile_key_lines(config) -> List[str]:
    lines = []
    for profile in list_profiles(config):
        marker = "* " if profile.id == (config.llm.get("active_profile") or config.active_model_profile) else "  "
        source = describe_key_source(profile.api_key, profile.api_key_source)
        lines.append(f"{marker}{profile.id}  key={mask_key(profile.api_key)}  source={source}")
    return lines


def _list_provider_lines(config: Config) -> List[str]:
    lines = []
    for provider in config.llm["providers"]:
        marker = "* " if provider["name"] == config.llm["active_provider"] else "  "
        model_names = ", ".join(m["name"] for m in provider["models"])
        lines.append(f"{marker}{provider['name']}  base_url={provider.get('base_url', '')}  models=[{model_names}]")
    return lines


def _resolve_workspace_target(config, name_or_path: str) -> Optional[str]:
    """Resolve a workspace move target: bookmark name first, then path."""
    name_or_path = (name_or_path or "").strip()
    if not name_or_path:
        return None
    lowered = name_or_path.lower()
    for bookmark in config.workspace_bookmarks:
        if bookmark["name"].lower() == lowered:
            return bookmark["path"]
    return name_or_path


def _list_models_for_provider(config: Config, provider_name: str) -> List[str]:
    provider = config._get_provider(provider_name)
    if not provider:
        return []
    return [m["name"] for m in provider["models"]]


def _choose_provider(config: Config, prompt: str = "Select provider") -> Optional[str]:
    names = _list_provider_lines(config)
    if not names:
        notice("No providers configured. Use /settings > Providers.")
        return None
    idx = select(prompt, names)
    if idx < 0:
        return None
    return config.llm["providers"][idx]["name"]


def _switch_after_save(agent, draft: ConfigDraft, report_text: str) -> str:
    """After apply_to, sync conversations context + runtime state."""
    config = agent.config
    config._sync_runtime_fields()
    agent.conversations.set_context_window(config.context_window)
    agent.conversations.update_runtime_state(
        model_profile=config.active_model_profile,
        authorization_level=config.authorization_level,
    )
    agent.conversations.save_all(reason="model_config_update")
    return report_text


def _run_test_result_message(result: ProviderTestResult) -> str:
    return result.summary() + ("" if result.ok else f"\nDetail: {result.provider_message}")


# ---- Provider wizard / add -----------------------------------------------------


def handle_providers(agent, raw: str, parts: List[str]) -> CommandResult:
    """Show all providers in draft-view."""
    banner("Configured Providers")
    lines = _list_provider_lines(agent.config) or ["(none)"]
    notice("\n".join(lines))
    notice("Use /settings > Providers to manage providers.")
    notice("Use /settings > Models to manage models.")
    return CommandResult(handled=True, success=True, data={"kind": "providers"})


def handle_provider_add(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config

    name = ask("Provider name (unique)")
    if not name:
        return CommandResult(handled=True, success=False, message="Provider name is required.")
    if config._get_provider(name):
        return CommandResult(handled=True, success=False, message=f"Provider '{name}' already exists.")
    base_url = ask("Base URL (https://...)", default="https://api.openai.com/v1")
    api_key_mode = ask_choice("API key mode", ["env", "inline", "empty"], default="env")
    api_key_env = ""
    api_key = ""
    if api_key_mode == "env":
        api_key_env = ask("API key env name", default=f"KAIRO_{name.upper().replace('-', '_')}_API_KEY")
    elif api_key_mode == "inline":
        api_key = ask("API key value (will be saved to config.json)")

    model_name = ask("Model name (required)")
    if not model_name:
        return CommandResult(handled=True, success=False, message="At least one model name is required.")

    context_window = ask_int("Context window", default=int(config.llm["defaults"]["context_window"]), minimum=1)
    max_tokens = ask_int("Max tokens", default=int(config.llm["defaults"]["max_tokens"]), minimum=1)
    temperature = ask_float("Temperature", default=float(config.llm["defaults"]["temperature"]), minimum=0.0, maximum=2.0)

    draft = ConfigDraft.from_config(config)
    if not draft.add_provider(
        name=name,
        base_url=base_url,
        api_key=api_key if api_key else "",
        api_key_env=api_key_env,
        models=[{
            "name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "context_window": context_window,
        }],
    ):
        return CommandResult(handled=True, success=False, message="Failed to add provider to draft.")

    if api_key_mode == "inline":
        notice("WARNING: inline API keys are written to config.json.")
        if not confirm("This will save the API key to disk. Continue?", default=False):
            return CommandResult(handled=True, success=False, message="Cancelled; no changes were saved.")

    if confirm("Test connection now?", default=True):
        _test_and_show(agent, base_url=base_url, api_key=api_key or _env_value(api_key_env), model=model_name)

    if not confirm("Save and switch to new model?", default=True):
        return CommandResult(handled=True, success=True, message="Draft discarded; no changes saved.")

    draft.set_active_model(name, model_name)
    allow_inline = api_key_mode == "inline"
    report = draft.apply_to(
        config,
        backup=True,
        allow_inline_key=allow_inline,
    )
    if not report.ok:
        error(report.to_text())
        return CommandResult(handled=True, success=False, message="Saved refused:\n" + report.to_text())

    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Provider '{name}' added and saved. Active target: {config.active_model_profile}",
        refresh_ui=True,
        data={"kind": "provider_saved"},
    )


def handle_provider_edit(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    target = _choose_provider(config)
    if not target:
        return CommandResult(handled=True, success=False, message="No provider selected.")
    provider = config._get_provider(target)

    new_name = ask("Rename to (blank keeps current)", default=target)
    base_url = ask("Base URL", default=provider.get("base_url", ""))
    api_key_mode = ask_choice("API key mode", ["env", "inline", "empty"], default="env" if provider.get("api_key_env") else "inline")
    api_key_env = ""
    api_key_arg: Any = None
    if api_key_mode == "env":
        api_key_env = ask("API key env name", default=provider.get("api_key_env", ""))
        api_key_arg = None  # leave inline key untouched
    elif api_key_mode == "inline":
        # blank keeps existing (0.2.6-beta); non-empty replaces.
        api_key_arg = ask("API key value (blank keeps existing)")
    elif api_key_mode == "empty":
        api_key_arg = KEY_CLEAR  # explicit clear

    draft = ConfigDraft.from_config(config)
    rename = new_name.strip() or None
    draft.update_provider(
        target,
        base_url=base_url,
        api_key=api_key_arg,
        api_key_env=api_key_env,
        rename=rename,
    )

    if api_key_mode == "inline" and api_key_arg:
        if not confirm("This will save the API key to disk. Continue?", default=False):
            return CommandResult(handled=True, success=False, message="Cancelled; inline key not saved.")
        allow_inline = True
    else:
        allow_inline = False

    report = draft.apply_to(
        config,
        backup=True,
        allow_inline_key=allow_inline,
    )
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Provider '{target}' updated and saved.",
        refresh_ui=True,
        data={"kind": "provider_saved"},
    )


def handle_provider_remove(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    if len(config.llm["providers"]) <= 1:
        return CommandResult(handled=True, success=False, message="Cannot remove the last provider.")
    target = _choose_provider(config, prompt="Remove which provider")
    if not target:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    if not confirm(f"Remove provider '{target}' and all its models?", default=False):
        return CommandResult(handled=True, success=False, message="Cancelled; no changes saved.")
    draft = ConfigDraft.from_config(config)
    if not draft.remove_provider(target):
        return CommandResult(handled=True, success=False, message=f"Failed to remove '{target}'.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Provider '{target}' removed and saved.",
        refresh_ui=True,
        data={"kind": "provider_saved"},
    )


def handle_provider_test(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    target = _choose_provider(config, prompt="Test which provider")
    if not target:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    provider = config._get_provider(target)
    model_name = ask("Model to test (blank uses active)", default=config.llm["active_model"] if config.llm["active_provider"] == target else None)
    if not model_name and provider["models"]:
        model_name = provider["models"][0]["name"]
    if not model_name:
        return CommandResult(handled=True, success=False, message="No model to test.")
    api_key = ask("API key (blank uses env/inline from config)", default="")
    if not api_key:
        env_name = provider.get("api_key_env", "")
        api_key = _env_value(env_name) if env_name else provider.get("api_key", "")
    result = _test_and_show(agent, base_url=provider.get("base_url", ""), api_key=api_key, model=model_name)
    return CommandResult(
        handled=True,
        success=result.ok,
        message=_run_test_result_message(result),
        interactive=True,
        data={"kind": "provider_test_result", "result": result},
    )


# ---- Model add/edit/remove/test -----------------------------------------------


def handle_model_add(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    provider_name = _choose_provider(config, prompt="Add model to which provider")
    if not provider_name:
        return CommandResult(handled=True, success=False, message="No provider selected.")
    name = ask("Model name (required)")
    if not name:
        return CommandResult(handled=True, success=False, message="Model name is required.")
    context_window = ask_int("Context window", default=int(config.llm["defaults"]["context_window"]), minimum=1)
    max_tokens = ask_int("Max tokens", default=int(config.llm["defaults"]["max_tokens"]), minimum=1)
    temperature = ask_float("Temperature", default=float(config.llm["defaults"]["temperature"]), minimum=0.0, maximum=2.0)

    draft = ConfigDraft.from_config(config)
    if not draft.add_model(provider_name, name=name, temperature=temperature, max_tokens=max_tokens, context_window=context_window):
        return CommandResult(handled=True, success=False, message="Failed to add model (duplicate or missing provider).")

    if not confirm("Save?", default=True):
        return CommandResult(handled=True, success=False, message="Draft discarded; no changes saved.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Model '{name}' added to '{provider_name}' and saved.",
        refresh_ui=True,
        data={"kind": "model_saved"},
    )


def handle_model_edit(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    provider_name = _choose_provider(config, prompt="Edit model in which provider")
    if not provider_name:
        return CommandResult(handled=True, success=False, message="No provider selected.")
    models = _list_models_for_provider(config, provider_name)
    if not models:
        return CommandResult(handled=True, success=False, message="Provider has no models.")
    model_idx = select("Edit which model", models)
    if model_idx < 0:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    model_name = models[model_idx]
    current = config._get_model(provider_name, model_name)

    new_name = ask("Rename to (blank keeps)", default=model_name)
    context_window = ask_int(
        "Context window",
        default=int(current.get("context_window", config.llm["defaults"]["context_window"])),
        minimum=1,
    )
    max_tokens = ask_int(
        "Max tokens",
        default=int(current.get("max_tokens", config.llm["defaults"]["max_tokens"])),
        minimum=1,
    )
    temperature = ask_float(
        "Temperature",
        default=float(current.get("temperature", config.llm["defaults"]["temperature"])),
        minimum=0.0,
        maximum=2.0,
    )

    draft = ConfigDraft.from_config(config)
    draft.update_model(
        provider_name,
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        context_window=context_window,
        rename=new_name.strip() or None,
    )
    if not confirm("Save?", default=True):
        return CommandResult(handled=True, success=False, message="Draft discarded; no changes saved.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Model '{model_name}' updated and saved.",
        refresh_ui=True,
        data={"kind": "model_saved"},
    )


def handle_model_remove(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    provider_name = _choose_provider(config, prompt="Remove model from which provider")
    if not provider_name:
        return CommandResult(handled=True, success=False, message="No provider selected.")
    models = _list_models_for_provider(config, provider_name)
    if len(models) <= 1:
        return CommandResult(handled=True, success=False, message="Provider has only one model; cannot remove.")
    model_idx = select("Remove which model", models)
    if model_idx < 0:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    model_name = models[model_idx]
    if not confirm(f"Remove model '{model_name}' from '{provider_name}'?", default=False):
        return CommandResult(handled=True, success=False, message="Cancelled; no changes saved.")
    draft = ConfigDraft.from_config(config)
    if not draft.remove_model(provider_name, model_name):
        return CommandResult(handled=True, success=False, message="Failed to remove model.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Model '{model_name}' removed and saved.",
        refresh_ui=True,
        data={"kind": "model_saved"},
    )


def handle_model_test(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    provider_name = _choose_provider(config, prompt="Test model in which provider")
    if not provider_name:
        return CommandResult(handled=True, success=False, message="No provider selected.")
    models = _list_models_for_provider(config, provider_name)
    if not models:
        return CommandResult(handled=True, success=False, message="Provider has no models.")
    model_idx = select("Test which model", models)
    if model_idx < 0:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    model_name = models[model_idx]
    provider = config._get_provider(provider_name)
    env_name = provider.get("api_key_env", "")
    api_key = _env_value(env_name) if env_name else provider.get("api_key", "")
    result = _test_and_show(agent, base_url=provider.get("base_url", ""), api_key=api_key, model=model_name)
    return CommandResult(
        handled=True,
        success=result.ok,
        message=_run_test_result_message(result),
        interactive=True,
        data={"kind": "provider_test_result", "result": result},
    )


# ---- Settings menu -------------------------------------------------------------


_SETTINGS_OPTIONS = [
    "Providers",
    "Models",
    "Keys",
    "Roles",
    "Config",
    "Doctor",
    "Exit",
]


def handle_settings(agent, raw: str, parts: List[str]) -> CommandResult:
    banner("Settings")
    idx = select("Choose an area", _SETTINGS_OPTIONS)
    if idx == 0:
        return _settings_providers_submenu(agent, raw, parts)
    if idx == 1:
        return _settings_models_submenu(agent, raw, parts)
    if idx == 2:
        return _settings_keys_submenu(agent, raw, parts)
    if idx == 3:
        return _settings_roles_submenu(agent, raw, parts)
    if idx == 4:
        return _settings_config_submenu(agent, raw, parts)
    if idx == 5:
        return handle_doctor(agent, raw, parts)
    return CommandResult(handled=True, success=True, message="Settings closed.", data={"kind": "settings"})


def _settings_providers_submenu(agent, raw: str, parts: List[str]) -> CommandResult:
    options = ["List providers", "Add provider", "Edit provider", "Remove provider", "Test provider", "Back"]
    idx = select("Providers", options)
    if idx == 0:
        return handle_providers(agent, raw, parts)
    if idx == 1:
        return handle_provider_add(agent, raw, parts)
    if idx == 2:
        return handle_provider_edit(agent, raw, parts)
    if idx == 3:
        return handle_provider_remove(agent, raw, parts)
    if idx == 4:
        return handle_provider_test(agent, raw, parts)
    return CommandResult(handled=True, success=True, data={"kind": "settings"})


def _settings_models_submenu(agent, raw: str, parts: List[str]) -> CommandResult:
    options = ["Add model", "Edit model", "Remove model", "Test model", "Back"]
    idx = select("Models", options)
    if idx == 0:
        return handle_model_add(agent, raw, parts)
    if idx == 1:
        return handle_model_edit(agent, raw, parts)
    if idx == 2:
        return handle_model_remove(agent, raw, parts)
    if idx == 3:
        return handle_model_test(agent, raw, parts)
    return CommandResult(handled=True, success=True, data={"kind": "settings"})


def _settings_keys_submenu(agent, raw: str, parts: List[str]) -> CommandResult:
    options = ["List keys", "Set key", "Clear key", "Reveal key", "Migrate keys", "Back"]
    idx = select("Keys", options)
    if idx == 0:
        return handle_keys(agent, raw, parts)
    if idx == 1:
        return handle_key_set(agent, raw, parts)
    if idx == 2:
        return handle_key_clear(agent, raw, parts)
    if idx == 3:
        return handle_key_reveal(agent, raw, parts)
    if idx == 4:
        return handle_key_migrate(agent, raw, parts)
    return CommandResult(handled=True, success=True, data={"kind": "settings"})


def _settings_roles_submenu(agent, raw: str, parts: List[str]) -> CommandResult:
    options = ["List roles", "Set role", "Clear role", "Back"]
    idx = select("Roles", options)
    if idx == 0:
        return handle_roles(agent, raw, parts)
    if idx == 1:
        return handle_role_set(agent, raw, parts)
    if idx == 2:
        return handle_role_clear(agent, raw, parts)
    return CommandResult(handled=True, success=True, data={"kind": "settings"})


def _settings_config_submenu(agent, raw: str, parts: List[str]) -> CommandResult:
    options = ["Validate", "Backup", "Restore", "Import", "Export (redacted)", "Back"]
    idx = select("Config", options)
    if idx == 0:
        return handle_config_validate(agent, raw, parts)
    if idx == 1:
        return handle_config_backup(agent, raw, parts)
    if idx == 2:
        return handle_config_restore(agent, raw, parts)
    if idx == 3:
        path = ask("Path to import from")
        if not path:
            return CommandResult(handled=True, success=False, message="Path required.")
        return handle_config_import(agent, raw, ["", "", path])
    if idx == 4:
        return handle_config_export(agent, raw, parts)
    return CommandResult(handled=True, success=True, data={"kind": "settings"})


# ---- Config validate / backup / restore ----------------------------------------


def handle_config_validate(agent, raw: str, parts: List[str]) -> CommandResult:
    draft = ConfigDraft.from_config(agent.config)
    report = draft.validate()
    return CommandResult(
        handled=True,
        success=report.ok,
        message=report.to_text(),
        data={"kind": "config_validate", "report": report},
    )


def handle_config_backup(agent, raw: str, parts: List[str]) -> CommandResult:
    backup_path = Config.create_backup(agent.config.config_path)
    if not backup_path:
        return CommandResult(handled=True, success=False, message="Failed to create backup.")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Backup written: {backup_path}",
        data={"kind": "config_backup", "path": str(backup_path)},
    )


def handle_config_restore(agent, raw: str, parts: List[str]) -> CommandResult:
    backups = Config.list_backups(agent.config.config_path)
    if not backups:
        return CommandResult(handled=True, success=False, message="No backups available.")
    options = [f"{b['name']}  ({b['size']} bytes)" for b in backups]
    idx = select("Choose a backup to restore", options)
    if idx < 0:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    chosen = backups[idx]["name"]
    if not confirm(f"Restore {chosen}? This OVERWRITES config.json.", default=False):
        return CommandResult(handled=True, success=False, message="Cancelled; no changes made.")
    if not Config.restore_backup(agent.config.config_path, chosen):
        return CommandResult(handled=True, success=False, message="Restore failed.")
    agent.config.load()
    agent.config._sync_runtime_fields()
    agent.conversations.set_context_window(agent.config.context_window)
    agent.conversations.update_runtime_state(model_profile=agent.config.active_model_profile)
    agent.conversations.save_all(reason="config_restore")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Restored {chosen} and reloaded config.",
        refresh_ui=True,
        data={"kind": "config_restore"},
    )


# ---- Docs ----------------------------------------------------------------------
DOCS_MAP: Dict[str, str] = {
    "": "docs/index.md",
    "config": "docs/configuration.md",
    "providers": "docs/configuration.md",
    "sessions": "docs/zh/user-manual.md",
}


def handle_docs(agent, raw: str, parts: List[str]) -> CommandResult:
    topic = parts[1].strip() if len(parts) > 1 else ""
    if not topic:
        notice("Available topics: config, providers, sessions")
        notice("Local docs:")
        notice("  docs/zh/user-manual.md")
        notice("  docs/en/user-manual.md")
        notice("  docs/commands.md")
        notice("  docs/configuration.md")
        return CommandResult(handled=True, success=True, message="Docs topics listed.", data={"kind": "docs"})
    if topic not in DOCS_MAP:
        notice(f"Unknown docs topic: '{topic}'. Available: config, providers, sessions")
        return CommandResult(handled=True, success=True, data={"kind": "docs"})
    target = DOCS_MAP[topic]
    path = _resolve_doc_path(target)
    if path:
        notice(f"Topic '{topic}':")
        notice(str(path))
    else:
        notice(f"No local doc for topic '{topic}'.")
    return CommandResult(handled=True, success=True, data={"kind": "docs", "topic": topic})


def _resolve_doc_path(rel: str) -> Optional[Path]:
    candidate = Path(rel)
    if candidate.exists():
        return candidate.resolve()
    # Try relative to the workspace root.
    workspace_root = Path.cwd()
    candidate2 = workspace_root / rel
    if candidate2.exists():
        return candidate2.resolve()
    return None


# ---- Helpers ------------------------------------------------------------------


def _env_value(env_name: str) -> str:
    if not env_name:
        return ""
    import os as _os

    return _os.environ.get(env_name, "")


def _test_and_show(agent, *, base_url: str, api_key: str, model: str) -> ProviderTestResult:
    notice(f"Testing {model} at {base_url} ...")
    result = test_connection(base_url=base_url, api_key=api_key, model=model)
    notice(_run_test_result_message(result))
    return result


def show_validation_issue(agent, report_text: str) -> None:
    """Plain-mode helper to surface validation output; modals read CommandResult directly."""
    notice(report_text)


# ---- Session management -------------------------------------------------------


def handle_session_rename(agent, raw: str, parts: List[str]) -> CommandResult:
    store = _get_session_store(agent)
    if store is None:
        return CommandResult(handled=True, success=False, message="Session persistence is disabled.")
    session = agent.conversations.active
    # No default so blank input is rejected (default would fill with current name).
    new_name = ask("New session name:")
    if not new_name:
        return CommandResult(handled=True, success=False, message="Session name cannot be empty.")
    if not store.rename_session(session.id, new_name):
        return CommandResult(handled=True, success=False, message="Failed to rename session.")
    session.name = new_name
    session.touch()
    agent.conversations.refresh_context()
    try:
        store.save_session(session, is_active=True, reason="session_rename")
    except Exception as exc:
        notice(f"Index sync warning: {exc}")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Session renamed to '{new_name}'.",
        refresh_ui=True,
        data={"kind": "session_rename", "name": new_name},
    )


def handle_session_delete(agent, raw: str, parts: List[str]) -> CommandResult:
    store = _get_session_store(agent)
    if store is None:
        return CommandResult(handled=True, success=False, message="Session persistence is disabled.")
    if len(agent.conversations.sessions) <= 1:
        return CommandResult(handled=True, success=False, message="Cannot delete the last session; create a new session first.")
    options = _session_options(agent)
    idx = select("Delete which session", options)
    if idx < 0:
        return CommandResult(handled=True, success=False, message="Cancelled.")
    target = agent.conversations.sessions[idx]
    if not confirm(f"Delete '{target.name}'?", default=False):
        return CommandResult(handled=True, success=False, message="Cancelled; no changes made.")
    if not store.delete_session(target.id):
        return CommandResult(handled=True, success=False, message="Failed to delete session.")
    agent.conversations.sessions = [s for s in agent.conversations.sessions if s.id != target.id]
    if agent.conversations.active_session_id == target.id:
        agent.conversations.active_session_id = agent.conversations.sessions[0].id
        agent.conversations.refresh_context()
    agent.conversations.save_active(reason="session_delete")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Session '{target.name}' deleted.",
        refresh_ui=True,
        data={"kind": "session_delete"},
    )


def handle_session_export(agent, raw: str, parts: List[str]) -> CommandResult:
    store = _get_session_store(agent)
    if store is None:
        return CommandResult(handled=True, success=False, message="Session persistence is disabled.")
    fmt = ask_choice("Export format", ["markdown", "json"], default="markdown")
    session = agent.conversations.active
    dest = store.export_session(session.id, fmt=fmt)
    if not dest:
        return CommandResult(handled=True, success=False, message="Export failed.")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Exported '{session.name}' ({fmt}) to:\n{dest}",
        data={"kind": "session_export", "path": str(dest), "format": fmt},
    )


def handle_session_reveal(agent, raw: str, parts: List[str]) -> CommandResult:
    store = _get_session_store(agent)
    if store is None:
        return CommandResult(handled=True, success=False, message="Session persistence is disabled.")
    path = store.reveal_session_path(agent.conversations.active.id)
    if not path:
        return CommandResult(handled=True, success=False, message="Active session has no on-disk file.")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Session '{agent.conversations.active.name}' file:\n{path}",
        data={"kind": "session_reveal", "path": str(path)},
    )


def _get_session_store(agent):
    store = getattr(agent.conversations, "session_store", None)
    if store is None:
        return None
    return store


def _session_options(agent) -> List[str]:
    options = []
    for session in agent.conversations.sessions:
        marker = "*" if session.id == agent.conversations.active_session_id else " "
        options.append(f"{marker} {session.name} | {len(session.history)} messages")
    return options


# ---- New 0.2.7-beta interactive handlers --------------------------------------


def handle_setup(agent, raw: str, parts: List[str]) -> CommandResult:
    """First-run setup wizard that creates a chat profile and saves config."""
    config = agent.config
    profiles = config.get_profile_ids()
    if profiles:
        if not confirm("Configuration already has profiles. Create a new profile anyway?", default=False):
            return CommandResult(handled=True, success=False, message="Setup cancelled.")

    provider_name = ask("Provider name")
    if not provider_name:
        return CommandResult(handled=True, success=False, message="Provider name is required.")

    base_url = ask("Base URL", default="https://api.openai.com/v1")
    model_name = ask("Model name")
    if not model_name:
        return CommandResult(handled=True, success=False, message="Model name is required.")

    api_key_mode = ask_choice("API key mode", ["env", "inline", "empty"], default="env")
    api_key_env = ""
    api_key = ""
    if api_key_mode == "env":
        api_key_env = ask("API key env name", default=f"KAIRO_{provider_name.upper().replace('-', '_')}_API_KEY")
    elif api_key_mode == "inline":
        api_key = ask("API key value (will be saved to config.json)")

    context_window = ask_int("Context window", default=int(config.llm["defaults"]["context_window"]), minimum=1)
    max_tokens = ask_int("Max tokens", default=int(config.llm["defaults"]["max_tokens"]), minimum=1)
    temperature = ask_float("Temperature", default=float(config.llm["defaults"]["temperature"]), minimum=0.0, maximum=2.0)

    profile_id = f"{provider_name}/{model_name}"
    draft = ConfigDraft.from_config(config)
    if not draft.add_profile(
        id=profile_id,
        provider=provider_name,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        context_window=context_window,
    ):
        return CommandResult(handled=True, success=False, message=f"Failed to add profile '{profile_id}'.")

    allow_inline = False
    if api_key_mode == "inline":
        notice("WARNING: inline API keys are written to config.json.")
        if not confirm("This will save the API key to disk. Continue?", default=False):
            return CommandResult(handled=True, success=False, message="Cancelled; no changes saved.")
        allow_inline = True

    if confirm("Test connection now?", default=True):
        test_key = api_key or _env_value(api_key_env)
        _test_and_show(agent, base_url=base_url, api_key=test_key, model=model_name)

    if not confirm("Save and switch to this profile?", default=True):
        return CommandResult(handled=True, success=True, message="Draft discarded; no changes saved.")

    draft.set_active_profile(profile_id)
    report = draft.apply_to(config, backup=True, allow_inline_key=allow_inline)
    if not report.ok:
        error(report.to_text())
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())

    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Profile '{profile_id}' created and saved. Active target: {config.active_model_profile}",
        refresh_ui=True,
        data={"kind": "setup"},
    )


def handle_mode(agent, raw: str, parts: List[str]) -> CommandResult:
    """Interactive mode switcher for authorization, plan and thinking."""
    config = agent.config
    options = [
        "Toggle Authorization",
        "Toggle Plan Mode",
        "Toggle Thinking Mode",
        "Done",
    ]
    while True:
        banner("Mode Settings")
        notice(f"Authorization: {config.authorization_level.upper()}")
        notice(f"Plan Mode: {'ON' if config.plan_mode else 'OFF'}")
        notice(f"Thinking Mode: {'ON' if config.thinking_mode else 'OFF'}")
        idx = select("Choose an option", options)
        if idx < 0 or idx == 3:
            return CommandResult(handled=True, success=True, message="Mode settings closed.", data={"kind": "mode"})

        draft = ConfigDraft.from_config(config)
        if idx == 0:
            current = config.authorization_level
            nxt = {"manual": "auto", "auto": "yolo", "yolo": "manual"}.get(current, "manual")
            draft.authorization_level = nxt
        elif idx == 1:
            draft.plan_mode = not config.plan_mode
        elif idx == 2:
            draft.thinking_mode = not config.thinking_mode

        report = draft.apply_to(config, backup=True)
        if not report.ok:
            error(report.to_text())
            return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
        _switch_after_save(agent, draft, report.to_text())
        notice("Saved.")


def handle_status(agent, raw: str, parts: List[str]) -> CommandResult:
    """Read-only runtime status summary with redacted secrets."""
    cfg = agent.config
    from agent.profile_resolver import get_active_profile

    active = get_active_profile(cfg)
    key_hint = cfg.describe_active_api_key()

    try:
        tracker = agent.conversations.active.token_tracker
        ctx_used = tracker.context_used_tokens
        ctx_window = tracker.context_window
        ctx_pct = tracker.context_percent
    except Exception:
        ctx_used = 0
        ctx_window = cfg.context_window
        ctx_pct = 0.0

    session = agent.conversations.active
    session_id_short = session.id[:8] if hasattr(session, "id") else ""

    lines = [
        "Kairo version: 0.2.7-beta",
        f"Active profile: {active.id if active else 'none'}",
        f"Model: {cfg.model}",
        f"Base URL: {cfg.base_url}",
        f"API Key: {key_hint}",
        "",
        f"Session: {agent.active_session_name}",
        f"Session ID: {session_id_short}...",
        f"Messages: {len(agent.history)}",
        f"Context: {ctx_used}/{ctx_window} ({ctx_pct:.1f}%)",
        "",
        f"Workspace: {agent.workspace_context.root}",
        f"Authorization: {cfg.authorization_level.upper()}",
        f"Plan Mode: {'ON' if cfg.plan_mode else 'OFF'}",
        f"Thinking Mode: {'ON' if cfg.thinking_mode else 'OFF'}",
        f"Session persistence: {'ON' if cfg.sessions.get('enabled') else 'OFF'}",
        f"Strict message packing: {'ON' if cfg.strict_message_packing else 'OFF'}",
        f"Esc stops generation: {'ON' if cfg.ui.get('esc_stops_generation') else 'OFF'}",
    ]
    text = "\n".join(lines)
    notice(text)
    return CommandResult(handled=True, success=True, message=text, data={"kind": "status"})


def handle_find(agent, raw: str, parts: List[str]) -> CommandResult:
    """Search sessions and optionally open one by index."""
    keyword = parts[2].strip() if len(parts) > 2 else ""
    if not keyword:
        keyword = ask("Search keyword")
    if not keyword:
        return CommandResult(handled=True, success=False, message="Search keyword is required.")

    results = _search_sessions(agent, keyword)
    if not results:
        return CommandResult(handled=True, success=True, message=f"No sessions matched '{keyword}'.", data={"kind": "find", "results": []})

    notice(f"Search results for '{keyword}':")
    lines = [f"[{r['index']}] {r['name']}  ({r['path']})" for r in results]
    notice("\n".join(lines))

    choice = ask("Enter index to open (blank to cancel)")
    if not choice:
        return CommandResult(handled=True, success=True, message="Cancelled.", data={"kind": "find", "results": results})
    try:
        idx = int(choice)
    except ValueError:
        return CommandResult(handled=True, success=False, message="Invalid index.", data={"kind": "find", "results": results})
    if idx < 0 or idx >= len(results):
        return CommandResult(handled=True, success=False, message="Index out of range.", data={"kind": "find", "results": results})

    session_id = results[idx]["id"]
    agent.conversations.switch_session(session_id)
    return CommandResult(
        handled=True,
        success=True,
        message=f"Switched to session: {agent.conversations.active.name}",
        refresh_ui=True,
        data={"kind": "find", "session_id": session_id},
    )


def handle_export(agent, raw: str, parts: List[str]) -> CommandResult:
    """Unified export menu for sessions and config."""
    options = [
        "Export current session as Markdown",
        "Export current session as JSON",
        "Export config (redacted)",
        "Export config with keys",
        "Cancel",
    ]
    idx = select("Choose export option", options)
    if idx == 0:
        return handle_session_export(agent, raw, parts)
    if idx == 1:
        store = _get_session_store(agent)
        if store is None:
            return CommandResult(handled=True, success=False, message="Session persistence is disabled.")
        session = agent.conversations.active
        dest = store.export_session(session.id, fmt="json")
        if not dest:
            return CommandResult(handled=True, success=False, message="Export failed.")
        return CommandResult(
            handled=True,
            success=True,
            message=f"Exported '{session.name}' (json) to:\n{dest}",
            data={"kind": "export", "path": str(dest), "format": "json"},
        )
    if idx == 2:
        return handle_config_export(agent, raw, parts)
    if idx == 3:
        return handle_config_export(agent, raw, parts + ["--with-keys"])
    return CommandResult(handled=True, success=True, message="Export cancelled.", data={"kind": "export"})


# ---- Session management panel -------------------------------------------------


def handle_sessions(agent, raw: str, parts: List[str]) -> CommandResult:
    """Session management panel."""
    options = ["Switch", "Search", "Open", "Rename", "Delete", "Export", "Reveal path", "Back"]
    idx = select("Session management", options)
    if idx == 0:
        switch_options = _session_options(agent)
        idx2 = select("Switch to which session", switch_options)
        if idx2 < 0:
            return CommandResult(handled=True, success=True, message="Cancelled.", data={"kind": "sessions"})
        if idx2 >= len(agent.conversations.sessions):
            return CommandResult(
                handled=True,
                success=False,
                message="Session switch cancelled: invalid selection.",
                data={"kind": "sessions"},
            )
        session = agent.conversations.sessions[idx2]
        agent.conversations.switch_session(session.id)
        return CommandResult(
            handled=True,
            success=True,
            message=f"Switched to session: {session.name}",
            refresh_ui=True,
            data={"kind": "sessions"},
        )
    if idx == 1:
        keyword = ask("Search keyword")
        if not keyword:
            return CommandResult(handled=True, success=False, message="Keyword required.")
        return handle_find(agent, raw, ["", "", keyword])
    if idx == 2:
        target = ask("Session id or index")
        if not target:
            return CommandResult(handled=True, success=False, message="Target required.")
        return handle_session_open(agent, raw, ["", "", target])
    if idx == 3:
        return handle_session_rename(agent, raw, parts)
    if idx == 4:
        return handle_session_delete(agent, raw, parts)
    if idx == 5:
        return handle_session_export(agent, raw, parts)
    if idx == 6:
        return handle_session_reveal(agent, raw, parts)
    return CommandResult(handled=True, success=True, message="Back.", data={"kind": "sessions"})


# ---- Workspace management panel -----------------------------------------------


def _workspace_tree_review(agent):
    from agent.workspace import WorkspaceMonitor
    monitor = WorkspaceMonitor(agent.workspace_context.root)
    snapshot = monitor.refresh()
    files = list(snapshot.files[:50])
    notice(f"Workspace tree: {snapshot.root}")
    if snapshot.tree_truncated:
        notice("(truncated)")
    notice("\n".join(files) or "(no files)")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Workspace tree: {snapshot.root}",
        data={"kind": "workspace", "files": files},
    )


def _workspace_changed_files(agent):
    from agent.workspace import WorkspaceMonitor
    monitor = WorkspaceMonitor(agent.workspace_context.root)
    snapshot = monitor.refresh()
    changes = snapshot.changes
    if not changes:
        notice("No changed files.")
        return CommandResult(handled=True, success=True, message="No changed files.", data={"kind": "workspace"})
    lines = [f"  {c.status} {c.path}{' *' if c.session_touched else ''}" for c in changes]
    notice("Changed files:")
    notice("\n".join(lines))
    return CommandResult(handled=True, success=True, message="\n".join(lines), data={"kind": "workspace"})


def _workspace_diff_viewer(agent):
    from agent.workspace import WorkspaceMonitor
    monitor = WorkspaceMonitor(agent.workspace_context.root)
    snapshot = monitor.refresh()
    changes = snapshot.changes
    if not changes:
        notice("No changed files to diff.")
        return CommandResult(handled=True, success=True, message="No changed files to diff.", data={"kind": "workspace"})
    options = [c.path for c in changes]
    idx = select("Select file to diff", options)
    if idx < 0:
        return CommandResult(handled=True, success=True, message="Cancelled.", data={"kind": "workspace"})
    selected = options[idx]
    snapshot = monitor.refresh(selected_file=selected)
    notice(f"Diff for {selected}:")
    notice(snapshot.diff)
    return CommandResult(
        handled=True,
        success=True,
        message=f"Diff for {selected}:\n{snapshot.diff}",
        data={"kind": "workspace"},
    )


def handle_workspace(agent, raw: str, parts: List[str]) -> CommandResult:
    """Workspace management panel."""
    options = [
        "Show current workspace",
        "Move workspace",
        "Save bookmark",
        "Remove bookmark",
        "List bookmarks",
        "Tree review",
        "Changed files",
        "Diff viewer",
        "Back",
    ]
    idx = select("Workspace management", options)
    if idx == 0:
        root = agent.workspace_context.root
        bookmarks = agent.config.workspace_bookmarks
        lines = [f"Current workspace: {root}"]
        if bookmarks:
            lines.append("Bookmarks:")
            for b in bookmarks:
                lines.append(f"  - {b['name']}: {b['path']}")
        else:
            lines.append("No bookmarks saved.")
        notice("\n".join(lines))
        return CommandResult(handled=True, success=True, message="\n".join(lines), data={"kind": "workspace"})
    if idx == 1:
        target = ask("Path or bookmark name")
        if not target:
            return CommandResult(handled=True, success=False, message="Target required.")
        resolved = _resolve_workspace_target(agent.config, target)
        if resolved is None:
            return CommandResult(handled=True, success=False, message="Target required.")
        return agent.move_workspace(resolved)
    if idx == 2:
        return handle_workspace_save(agent, raw, parts)
    if idx == 3:
        return handle_workspace_remove(agent, raw, parts)
    if idx == 4:
        return handle_workspaces(agent, raw, parts)
    if idx == 5:
        return _workspace_tree_review(agent)
    if idx == 6:
        return _workspace_changed_files(agent)
    if idx == 7:
        return _workspace_diff_viewer(agent)
    return CommandResult(handled=True, success=True, message="Back.", data={"kind": "workspace"})


# ---- Key management -----------------------------------------------------------


def handle_keys(agent, raw: str, parts: List[str]) -> CommandResult:
    lines = _list_profile_key_lines(agent.config) or ["(none)"]
    banner("API Key Status")
    notice("\n".join(lines))
    notice("Use /settings > Keys to manage API keys.")
    return CommandResult(
        handled=True,
        success=True,
        message="\n".join(lines),
        data={"kind": "keys", "lines": lines},
    )


def _choose_profile(agent, prompt: str = "Select profile") -> Optional[str]:
    ids = agent.config.get_profile_ids()
    if not ids:
        notice("No profiles configured.")
        return None
    idx = select(prompt, ids)
    if idx < 0:
        return None
    return ids[idx]


def handle_key_set(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    profile_id = parts[2].strip() if len(parts) > 2 else ""
    if not profile_id:
        profile_id = _choose_profile(agent, prompt="Set key for which profile")
    if not profile_id:
        return CommandResult(handled=True, success=False, message="No profile selected.")
    if profile_id not in config.get_profile_ids():
        return CommandResult(handled=True, success=False, message=f"Profile '{profile_id}' not found.")

    key = ask(f"API key for '{profile_id}' (will be saved to config.json)")
    if not key:
        return CommandResult(handled=True, success=False, message="No key entered.")

    notice("WARNING: inline API keys are written to config.json.")
    if not confirm("This will save the API key to disk. Continue?", default=False):
        return CommandResult(handled=True, success=False, message="Cancelled; no changes saved.")

    draft = ConfigDraft.from_config(config)
    if not draft.set_key(profile_id, key):
        return CommandResult(handled=True, success=False, message=f"Failed to set key for '{profile_id}'.")
    report = draft.apply_to(config, backup=True, allow_inline_key=True)
    if not report.ok:
        error(report.to_text())
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"API key set for '{profile_id}' and saved.",
        refresh_ui=True,
        data={"kind": "key_set", "profile_id": profile_id},
    )


def handle_key_clear(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    profile_id = parts[2].strip() if len(parts) > 2 else ""
    if not profile_id:
        profile_id = _choose_profile(agent, prompt="Clear key for which profile")
    if not profile_id:
        return CommandResult(handled=True, success=False, message="No profile selected.")
    if profile_id not in config.get_profile_ids():
        return CommandResult(handled=True, success=False, message=f"Profile '{profile_id}' not found.")

    draft = ConfigDraft.from_config(config)
    if not draft.clear_key(profile_id):
        return CommandResult(handled=True, success=False, message=f"Failed to clear key for '{profile_id}'.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"API key cleared for '{profile_id}'.",
        refresh_ui=True,
        data={"kind": "key_cleared", "profile_id": profile_id},
    )


def handle_key_reveal(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    profile_id = parts[2].strip() if len(parts) > 2 else ""
    if not profile_id:
        profile_id = _choose_profile(agent, prompt="Reveal key for which profile")
    if not profile_id:
        return CommandResult(handled=True, success=False, message="No profile selected.")

    from agent.profile_resolver import resolve_profile
    profile = resolve_profile(config, profile_id=profile_id)
    if profile is None:
        return CommandResult(handled=True, success=False, message=f"Profile '{profile_id}' not found.")
    if not profile.api_key:
        return CommandResult(handled=True, success=False, message=f"Profile '{profile_id}' has no key to reveal.")

    notice("WARNING: revealing an API key exposes it to the screen and may be recorded.")
    if not confirm("Reveal full key?", default=False):
        return CommandResult(handled=True, success=False, message="Reveal cancelled.")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Profile '{profile_id}' API key:\n{profile.api_key}",
        data={"kind": "key_reveal", "profile_id": profile_id, "key": profile.api_key},
    )


def handle_key_migrate(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    if not config.llm.get("providers"):
        return CommandResult(handled=True, success=False, message="No legacy providers to migrate keys from.")
    draft = ConfigDraft.from_config(config)
    plan = draft.migrate_keys()
    if not plan:
        return CommandResult(handled=True, success=True, message="No legacy keys to migrate.")
    notice("Migration plan:")
    for pid in plan:
        notice(f"  - copy key into profile '{pid}'")
    if not confirm("Migrate legacy provider keys into profile keys?", default=False):
        return CommandResult(handled=True, success=False, message="Migration cancelled.")
    report = draft.apply_to(config, backup=True, allow_inline_key=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Migrated keys for {len(plan)} profile(s) and saved.",
        refresh_ui=True,
        data={"kind": "key_migrate", "migrated": plan},
    )


# ---- Role management ----------------------------------------------------------


VALID_ROLES = {"chat", "plan", "compress", "fast"}


def handle_roles(agent, raw: str, parts: List[str]) -> CommandResult:
    draft = ConfigDraft.from_config(agent.config)
    roles = draft.list_roles()
    lines = [f"  - {role}: {target}" for role, target in roles.items()]
    if not lines:
        lines = ["  (none configured)"]
    notice("Model Roles")
    notice("\n".join(lines))
    notice("Use /settings > Roles to manage model roles.")
    return CommandResult(
        handled=True,
        success=True,
        message="\n".join(lines),
        data={"kind": "roles", "roles": roles},
    )


def handle_role_set(agent, raw: str, parts: List[str]) -> CommandResult:
    config = agent.config
    if len(parts) < 3:
        return CommandResult(handled=True, success=False, message="Usage: /role set <role> <profile>")
    args = parts[2].strip().split(maxsplit=1)
    if len(args) < 2:
        return CommandResult(handled=True, success=False, message="Usage: /role set <role> <profile>")
    role, profile_id = args[0].strip(), args[1].strip()
    if role not in VALID_ROLES:
        return CommandResult(handled=True, success=False, message=f"Unknown role '{role}'. Valid roles: {', '.join(sorted(VALID_ROLES))}.")
    if profile_id not in config.get_profile_ids():
        return CommandResult(handled=True, success=False, message=f"Profile '{profile_id}' not found.")

    draft = ConfigDraft.from_config(config)
    if not draft.set_role(role, profile_id):
        return CommandResult(handled=True, success=False, message=f"Failed to set role '{role}'.")
    report = draft.apply_to(config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Role '{role}' set to '{profile_id}'.",
        refresh_ui=True,
        data={"kind": "role_set", "role": role, "profile_id": profile_id},
    )


def handle_role_clear(agent, raw: str, parts: List[str]) -> CommandResult:
    if len(parts) < 3:
        return CommandResult(handled=True, success=False, message="Usage: /role clear <role>")
    role = parts[2].strip()
    if role not in VALID_ROLES:
        return CommandResult(handled=True, success=False, message=f"Unknown role '{role}'. Valid roles: {', '.join(sorted(VALID_ROLES))}.")

    draft = ConfigDraft.from_config(agent.config)
    if not draft.clear_role(role):
        return CommandResult(handled=True, success=False, message=f"Role '{role}' is not set.")
    report = draft.apply_to(agent.config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Role '{role}' cleared.",
        refresh_ui=True,
        data={"kind": "role_cleared", "role": role},
    )


# ---- Workspace bookmarks ------------------------------------------------------


def handle_workspace_save(agent, raw: str, parts: List[str]) -> CommandResult:
    name = parts[2].strip() if len(parts) > 2 else ""
    if not name:
        name = ask("Bookmark name")
    if not name:
        return CommandResult(handled=True, success=False, message="Bookmark name is required.")
    path = str(agent.workspace_context.root)
    draft = ConfigDraft.from_config(agent.config)
    if not draft.add_workspace_bookmark(name, path):
        return CommandResult(handled=True, success=False, message="Failed to add bookmark.")
    report = draft.apply_to(agent.config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Workspace bookmark '{name}' saved.",
        refresh_ui=True,
        data={"kind": "workspace_saved", "name": name, "path": path},
    )


def handle_workspaces(agent, raw: str, parts: List[str]) -> CommandResult:
    bookmarks = agent.config.workspace_bookmarks
    if not bookmarks:
        return CommandResult(
            handled=True,
            success=True,
            message="No workspace bookmarks. Use /workspace > Save bookmark to create one.",
            data={"kind": "workspaces", "bookmarks": []},
        )
    options = [f"{b['name']}: {b['path']}" for b in bookmarks]
    notice("Workspace bookmarks:")
    notice("\n".join(options))
    return CommandResult(
        handled=True,
        success=True,
        message="\n".join(options),
        data={"kind": "workspaces", "options": options, "bookmarks": bookmarks},
    )


def handle_workspace_remove(agent, raw: str, parts: List[str]) -> CommandResult:
    name = parts[2].strip() if len(parts) > 2 else ""
    if not name:
        name = ask("Bookmark name to remove")
    if not name:
        return CommandResult(handled=True, success=False, message="Bookmark name is required.")
    draft = ConfigDraft.from_config(agent.config)
    if not draft.remove_workspace_bookmark(name):
        return CommandResult(handled=True, success=False, message=f"Bookmark '{name}' not found.")
    report = draft.apply_to(agent.config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Workspace bookmark '{name}' removed.",
        refresh_ui=True,
        data={"kind": "workspace_removed", "name": name},
    )


# ---- Session search -----------------------------------------------------------


def _search_sessions(agent, keyword: str) -> List[Dict[str, Any]]:
    store = _get_session_store(agent)
    if store is None:
        return []
    keyword_lower = keyword.lower()
    results: List[Dict[str, Any]] = []
    for session in agent.conversations.sessions:
        hits = []
        name_match = keyword_lower in session.name.lower()
        for message in session.history:
            content = str(message.get("content", "") or "").lower()
            if keyword_lower in content:
                snippet = session.name if name_match else "history"
                hits.append(snippet)
                break
        if name_match or hits:
            path = store.reveal_session_path(session.id)
            results.append({
                "id": session.id,
                "name": session.name,
                "index": len(results),
                "path": str(path) if path else "",
            })
    return results


def handle_session_search(agent, raw: str, parts: List[str]) -> CommandResult:
    if len(parts) < 3:
        return CommandResult(handled=True, success=False, message="Usage: /session search <keyword>")
    keyword = parts[2].strip()
    if not keyword:
        return CommandResult(handled=True, success=False, message="Search keyword is required.")
    results = _search_sessions(agent, keyword)
    if not results:
        return CommandResult(handled=True, success=True, message=f"No sessions matched '{keyword}'.")
    lines = [f"[{r['index']}] {r['name']}  ({r['path']})" for r in results]
    return CommandResult(
        handled=True,
        success=True,
        message=f"Search results for '{keyword}':\n" + "\n".join(lines),
        interactive=True,
        data={"kind": "session_search", "results": results, "keyword": keyword},
    )


def handle_session_open(agent, raw: str, parts: List[str]) -> CommandResult:
    if len(parts) < 3:
        return CommandResult(handled=True, success=False, message="Usage: /session open <id-or-index>")
    target = parts[2].strip()
    session = None
    for s in agent.conversations.sessions:
        if s.id == target:
            session = s
            break
    if session is None:
        try:
            idx = int(target)
            # Try last search results stored on agent if available.
            search_results = getattr(agent, "_last_session_search_results", [])
            if search_results and 0 <= idx < len(search_results):
                target_id = search_results[idx]["id"]
                for s in agent.conversations.sessions:
                    if s.id == target_id:
                        session = s
                        break
        except ValueError:
            pass
    if session is None:
        return CommandResult(handled=True, success=False, message=f"Session '{target}' not found.")
    agent.conversations.switch_session(session.id)
    return CommandResult(
        handled=True,
        success=True,
        message=f"Switched to session: {session.name}",
        refresh_ui=True,
        data={"kind": "session_open", "session": session},
    )


# ---- Config import / export ---------------------------------------------------


def handle_config_export(agent, raw: str, parts: List[str]) -> CommandResult:
    with_keys = len(parts) > 2 and "--with-keys" in parts[2]
    dest_path: Optional[Path] = None
    if len(parts) > 2:
        candidate = parts[2].replace("--with-keys", "").strip()
        if candidate:
            dest_path = Path(candidate).expanduser()
    draft = ConfigDraft.from_config(agent.config)
    if with_keys:
        notice("WARNING: exported file will contain full API keys.")
        if not confirm("Export config with plaintext keys?", default=False):
            return CommandResult(handled=True, success=False, message="Export cancelled.")
    data = draft.export_config(with_keys=with_keys)
    if dest_path:
        dest = dest_path
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        export_dir = Path(agent.config.config_path).parent / ".kairo" / "config_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = export_dir / f"config.export.{timestamp}.json"
    try:
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
    except Exception as exc:
        return CommandResult(handled=True, success=False, message=f"Export failed: {exc}")
    return CommandResult(
        handled=True,
        success=True,
        message=f"Config exported to:\n{dest}",
        data={"kind": "config_export", "path": str(dest), "with_keys": with_keys},
    )


def handle_config_import(agent, raw: str, parts: List[str]) -> CommandResult:
    if len(parts) < 3:
        return CommandResult(handled=True, success=False, message="Usage: /config import <path>")
    path = parts[2].strip()
    source_path = Path(path).expanduser()
    if not source_path.exists():
        return CommandResult(handled=True, success=False, message=f"Import file not found: {path}")

    draft = ConfigDraft.from_config(agent.config)
    report = draft.import_config(str(source_path))
    if not report.ok:
        return CommandResult(
            handled=True,
            success=False,
            message="Import validation failed; current config was not overwritten.\n" + report.to_text(),
        )
    if not confirm(f"Import will overwrite config.json with '{path}'. Continue?", default=False):
        return CommandResult(handled=True, success=False, message="Import cancelled.")
    report = draft.apply_to(agent.config, backup=True)
    if not report.ok:
        return CommandResult(handled=True, success=False, message="Save refused:\n" + report.to_text())
    _switch_after_save(agent, draft, report.to_text())
    return CommandResult(
        handled=True,
        success=True,
        message=f"Config imported from '{path}' and saved.",
        refresh_ui=True,
        data={"kind": "config_import", "path": path},
    )


# ---- Doctor -------------------------------------------------------------------


def handle_doctor(agent, raw: str, parts: List[str], *, local_only: bool = False) -> CommandResult:
    from pathlib import Path
    from agent.profile_resolver import list_profiles

    config = agent.config
    checks: List[Dict[str, Any]] = []

    # Config parse.
    checks.append({"name": "config parse", "ok": config._load_error is None, "detail": "config loaded" if config._load_error is None else str(config._load_error)})

    # Duplicate profile ids.
    profile_ids = [p.get("id", "") for p in config.llm.get("profiles", [])]
    duplicates = {pid for pid in profile_ids if profile_ids.count(pid) > 1}
    checks.append({"name": "duplicate profile ids", "ok": not duplicates, "detail": "none" if not duplicates else f"duplicates: {', '.join(duplicates)}"})

    # Active profile exists.
    active_id = config.llm.get("active_profile") or config.active_model_profile
    ids = set(profile_ids) or set(config.get_profile_ids())
    checks.append({"name": "active profile", "ok": active_id in ids or not ids, "detail": f"active={active_id}" if active_id else "no active profile"})

    # Key missing.
    profiles = list_profiles(config)
    missing_keys = [p.id for p in profiles if not p.api_key]
    checks.append({"name": "api key", "ok": not missing_keys, "detail": f"missing for: {', '.join(missing_keys)}" if missing_keys else "all profiles have keys"})

    # Base URL scheme.
    bad_urls = [p.id for p in profiles if not (p.base_url.startswith("http://") or p.base_url.startswith("https://"))]
    checks.append({"name": "base url scheme", "ok": not bad_urls, "detail": f"bad: {', '.join(bad_urls)}" if bad_urls else "all http/https"})

    # Workspace root.
    try:
        ws = Path(config.workspace_root).expanduser()
        ws_ok = ws.exists() and os.access(ws, os.W_OK)
        checks.append({"name": "workspace root", "ok": ws_ok, "detail": str(ws)})
    except Exception as exc:
        checks.append({"name": "workspace root", "ok": False, "detail": str(exc)})

    # Session dir.
    try:
        store = _get_session_store(agent)
        sd_ok = bool(store and os.access(store.storage_dir, os.W_OK))
        checks.append({"name": "session dir", "ok": sd_ok, "detail": str(store.storage_dir) if store else "no store"})
    except Exception as exc:
        checks.append({"name": "session dir", "ok": False, "detail": str(exc)})

    # Git.
    import shutil
    git_ok = shutil.which("git") is not None
    checks.append({"name": "git available", "ok": git_ok, "detail": "git found" if git_ok else "git not found"})

    # Provider health probe (first profile only).
    # The TUI runs this in a worker via run_doctor_probe() to avoid freezing the UI thread.
    skip_probe = local_only
    if not skip_probe and profiles:
        p = profiles[0]
        try:
            result = test_connection(base_url=p.base_url, api_key=p.api_key, model=p.model)
            checks.append({"name": "provider probe", "ok": result.ok, "detail": result.summary()})
        except Exception as exc:
            checks.append({"name": "provider probe", "ok": False, "detail": f"probe failed: {exc}"})
    else:
        checks.append({"name": "provider probe", "ok": False, "detail": "skipped" if skip_probe else "no profiles"})

    ok_count = sum(1 for c in checks if c["ok"])
    lines = [f"{'OK ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}" for c in checks]
    message = f"Doctor ({ok_count}/{len(checks)} checks passed):\n" + "\n".join(lines)
    return CommandResult(
        handled=True,
        success=ok_count == len(checks),
        message=message,
        data={"kind": "doctor", "checks": checks, "local_only": skip_probe},
    )


def run_doctor_probe(agent) -> CommandResult:
    """Run the provider health probe portion of /doctor separately.

    Used by the TUI so network IO does not block the UI thread.
    """
    from agent.profile_resolver import list_profiles
    config = agent.config
    profiles = list_profiles(config)
    if not profiles:
        return CommandResult(
            handled=True,
            success=False,
            message="No profiles configured for probe.",
            data={"kind": "doctor_probe", "checks": []},
        )
    p = profiles[0]
    try:
        result = test_connection(base_url=p.base_url, api_key=p.api_key, model=p.model)
        check = {"name": "provider probe", "ok": result.ok, "detail": result.summary()}
    except Exception as exc:
        check = {"name": "provider probe", "ok": False, "detail": f"probe failed: {exc}"}
    return CommandResult(
        handled=True,
        success=check["ok"],
        message=f"{'OK ' if check['ok'] else 'FAIL'} {check['name']}: {check['detail']}",
        data={"kind": "doctor_probe", "checks": [check]},
    )


__all__ = [
    "DOCS_MAP",
    "handle_config_backup",
    "handle_config_export",
    "handle_config_import",
    "handle_config_restore",
    "handle_config_validate",
    "handle_docs",
    "handle_doctor",
    "handle_export",
    "handle_find",
    "handle_key_clear",
    "handle_key_migrate",
    "handle_key_reveal",
    "handle_key_set",
    "handle_keys",
    "handle_mode",
    "handle_model_add",
    "handle_model_edit",
    "handle_model_remove",
    "handle_model_test",
    "handle_provider_add",
    "handle_provider_edit",
    "handle_provider_remove",
    "handle_provider_test",
    "handle_providers",
    "handle_role_clear",
    "handle_role_set",
    "handle_roles",
    "handle_session_delete",
    "handle_session_export",
    "handle_session_open",
    "handle_session_rename",
    "handle_session_reveal",
    "handle_session_search",
    "handle_sessions",
    "handle_settings",
    "handle_setup",
    "handle_status",
    "handle_workspace",
    "handle_workspace_remove",
    "handle_workspace_save",
    "handle_workspaces",
]
