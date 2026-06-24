"""Runtime configuration editing for Kairo 0.2.3.

``ConfigDraft`` provides an in-memory mutable copy of the configuration that
can be validated and committed back to disk atomically. Drafts never persist
raw API keys borrowed from environment variables: only keys explicitly marked
as ``file`` source (inline keys the user chose to save) flow back to disk.

The editor layer is intentionally UI-agnostic; plain prompts and Textual
modals both build a draft, mutate it, and call :meth:`ConfigDraft.apply_to`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent.config import Config
from agent.provider_registry import (
    get_model,
    get_provider,
    make_model,
    make_provider,
    merge_model_defaults,
)


VALIDATION_OK = "ok"
VALIDATION_WARNING = "warning"
VALIDATION_ERROR = "error"


@dataclass
class ValidationReport:
    """Structured output of :meth:`ConfigDraft.validate`."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def to_text(self) -> str:
        lines: List[str] = []
        if not self.errors and not self.warnings:
            lines.append("Configuration is valid.")
        for message in self.errors:
            lines.append(f"[error] {message}")
        for message in self.warnings:
            lines.append(f"[warn]  {message}")
        return "\n".join(lines)


class ConfigDraft:
    """In-memory editable copy of a :class:`Config` instance.

    Mutations on the draft never touch disk; :meth:`apply_to` writes the draft
    back into a live ``Config`` (optionally creating a backup first) and rolls
    back on failure.
    """

    def __init__(self, source: Config):
        self._source = source
        self._reset_from(source)

    def _reset_from(self, source: Config) -> None:
        self.llm: Dict[str, Any] = copy.deepcopy(source.llm)
        self.context_management_defaults: Dict[str, Any] = copy.deepcopy(source.context_management_defaults)
        self.sessions: Dict[str, Any] = copy.deepcopy(source.sessions)
        self.ui: Dict[str, Any] = copy.deepcopy(source.ui)
        self.policy: Dict[str, Any] = copy.deepcopy(source.policy)
        self.workspace_root: str = source.workspace_root
        self.skills_dir: str = source.skills_dir
        self.shell_type: str = source.shell_type
        self.authorization_level: str = source.authorization_level
        self.plan_mode: bool = source.plan_mode
        self.thinking_mode: bool = source.thinking_mode
        self.extra_fields: Dict[str, Any] = copy.deepcopy(source._extra_fields)

    # ---- snapshot ----------------------------------------------------------------

    @classmethod
    def from_config(cls, source: Config) -> "ConfigDraft":
        return cls(source)

    # ---- provider/model mutations -------------------------------------------------

    def add_provider(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        models: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        clean = (name or "").strip()
        if not clean or get_provider(self.llm["providers"], clean):
            return False
        normalized = make_provider(
            name=clean,
            base_url=base_url,
            models=models or [],
            api_key=api_key,
            api_key_env=api_key_env,
            normalize_context_management=self._normalize_context_management,
        )
        if not normalized or not normalized["models"]:
            return False
        self.llm["providers"].append(normalized)
        self._ensure_active()
        return True

    def update_provider(
        self,
        name: str,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: Optional[str] = None,
        rename: Optional[str] = None,
    ) -> bool:
        provider = get_provider(self.llm["providers"], name)
        if not provider:
            return True  # no-op; caller decides if this is an error via validate
        if base_url is not None:
            provider["base_url"] = base_url.strip()
        if api_key is not None:
            provider["api_key"] = api_key
            provider["_api_key_source"] = "file" if api_key else ("env" if provider.get("api_key_env") else "none")
        if api_key_env is not None:
            env_value = api_key_env.strip()
            provider["api_key_env"] = env_value
            if api_key is None:
                provider["_api_key_source"] = "env" if env_value else "none"
        if rename is not None:
            new_name = rename.strip()
            if new_name and new_name != name and not get_provider(self.llm["providers"], new_name):
                provider["name"] = new_name
                if self.llm["active_provider"] == name:
                    self.llm["active_provider"] = new_name
        return True

    def remove_provider(self, name: str) -> bool:
        provider = get_provider(self.llm["providers"], name)
        if not provider:
            return False
        self.llm["providers"] = [p for p in self.llm["providers"] if p["name"] != name]
        if self.llm["active_provider"] == name:
            self._ensure_active()
        return True

    def add_model(
        self,
        provider_name: str,
        *,
        name: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
        context_management: Optional[Dict[str, Any]] = None,
    ) -> bool:
        provider = get_provider(self.llm["providers"], provider_name)
        if not provider:
            return False
        if get_model(provider, name):
            return False
        model = make_model(
            name=name,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=context_window,
            context_management=context_management,
            normalize_context_management=self._normalize_context_management,
        )
        model = merge_model_defaults(model, self.llm["defaults"])
        provider["models"].append(model)
        return True

    def update_model(
        self,
        provider_name: str,
        model_name: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
        context_management: Optional[Dict[str, Any]] = None,
        rename: Optional[str] = None,
    ) -> bool:
        provider = get_provider(self.llm["providers"], provider_name)
        if not provider:
            return True
        model = get_model(provider, model_name)
        if not model:
            return True
        if temperature is not None:
            model["temperature"] = float(temperature)
        if max_tokens is not None:
            model["max_tokens"] = int(max_tokens)
        if context_window is not None:
            model["context_window"] = int(context_window)
        if context_management is not None:
            model["context_management"] = self._normalize_context_management(context_management)
        if rename is not None:
            new_name = rename.strip()
            if new_name and new_name != model_name and not get_model(provider, new_name):
                model["name"] = new_name
                if self.llm["active_provider"] == provider_name and self.llm["active_model"] == model_name:
                    self.llm["active_model"] = new_name
        return True

    def remove_model(self, provider_name: str, model_name: str) -> bool:
        provider = get_provider(self.llm["providers"], provider_name)
        if not provider:
            return False
        if not get_model(provider, model_name):
            return False
        if len(provider["models"]) <= 1:
            return False
        provider["models"] = [m for m in provider["models"] if m["name"] != model_name]
        if self.llm["active_provider"] == provider_name and self.llm["active_model"] == model_name:
            self.llm["active_model"] = provider["models"][0]["name"]
        return True

    def set_active_model(self, provider_name: str, model_name: str) -> bool:
        provider = get_provider(self.llm["providers"], provider_name)
        if not provider or not get_model(provider, model_name):
            return False
        self.llm["active_provider"] = provider_name
        self.llm["active_model"] = model_name
        return True

    # ---- validation ---------------------------------------------------------------

    def validate(self) -> ValidationReport:
        report = ValidationReport()
        providers = self.llm.get("providers", [])
        if not providers:
            report.add_error("llm.providers is empty; at least one provider is required.")
            return report

        seen_providers: set = set()
        for provider in providers:
            name = str(provider.get("name", "")).strip()
            if not name:
                report.add_error("A provider is missing its name field.")
                continue
            if name in seen_providers:
                report.add_error(f"Duplicate provider name: {name}")
            seen_providers.add(name)
            base_url = str(provider.get("base_url", "")).strip()
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                report.add_error(f"Provider '{name}' base_url is not a valid http/https URL: {base_url!r}")
            models = provider.get("models", [])
            if not models:
                report.add_error(f"Provider '{name}' has no models defined.")
            seen_models: set = set()
            for model in models:
                model_name = str(model.get("name", "")).strip()
                if not model_name:
                    report.add_error(f"Provider '{name}' has a model missing its name.")
                    continue
                if model_name in seen_models:
                    report.add_error(f"Duplicate model name within provider '{name}': {model_name}")
                seen_models.add(model_name)
                try:
                    context_window = int(model.get("context_window", self.llm["defaults"]["context_window"]))
                except (TypeError, ValueError):
                    context_window = -1
                if context_window <= 0:
                    report.add_error(f"Provider '{name}' model '{model_name}' context_window must be > 0.")
                try:
                    max_tokens = int(model.get("max_tokens", self.llm["defaults"]["max_tokens"]))
                except (TypeError, ValueError):
                    max_tokens = -1
                if max_tokens <= 0:
                    report.add_error(f"Provider '{name}' model '{model_name}' max_tokens must be > 0.")
                if context_window > 0 and max_tokens > context_window:
                    report.add_error(
                        f"Provider '{name}' model '{model_name}' max_tokens ({max_tokens}) "
                        f"cannot exceed context_window ({context_window})."
                    )
                try:
                    temperature = float(model.get("temperature", self.llm["defaults"]["temperature"]))
                except (TypeError, ValueError):
                    temperature = -1.0
                if temperature < 0 or temperature > 2:
                    report.add_error(
                        f"Provider '{name}' model '{model_name}' temperature {temperature} "
                        "must be between 0 and 2."
                    )
            api_key = str(provider.get("api_key", ""))
            api_key_env = str(provider.get("api_key_env", "")).strip()
            if api_key and api_key_env:
                report.add_warning(
                    f"Provider '{name}' has both api_key (inline) and api_key_env set; "
                    "env value takes precedence at runtime, inline key will still be persisted."
                )

        active_provider = str(self.llm.get("active_provider", "")).strip()
        active_model = str(self.llm.get("active_model", "")).strip()
        if active_provider and not get_provider(providers, active_provider):
            report.add_warning(f"active_provider '{active_provider}' not found in providers list.")
            provider = providers[0]
            self.llm["active_provider"] = provider["name"]
            self.llm["active_model"] = provider["models"][0]["name"]
        else:
            provider = get_provider(providers, active_provider) if active_provider else None
            if provider and active_model and not get_model(provider, active_model):
                report.add_warning(f"active_model '{active_model}' not found under provider '{active_provider}'.")
                self.llm["active_model"] = provider["models"][0]["name"]
            elif not active_provider:
                self.llm["active_provider"] = providers[0]["name"]
                self.llm["active_model"] = providers[0]["models"][0]["name"]

        storage_dir = str(self.sessions.get("storage_dir", "")).strip()
        if not storage_dir:
            report.add_warning("sessions.storage_dir is empty; sessions may be disabled.")
        try:
            workspace_root = Path(self.workspace_root).expanduser()
            if not workspace_root.exists():
                report.add_warning(f"workspace_root does not exist: {workspace_root}")
        except Exception as exc:
            report.add_warning(f"workspace_root is invalid: {exc}")

        return report

    # ---- commit -------------------------------------------------------------------

    def apply_to(
        self,
        config: Config,
        *,
        backup: bool = True,
        allow_inline_key: bool = False,
        allowed_inline_providers: Optional[Iterable[str]] = None,
    ) -> ValidationReport:
        """Commit the draft to *config* and persist.

        - Runs validation first; refused if there are errors.
        - When ``backup=True`` writes a timestamped backup before overwriting.
        - ``allow_inline_key`` gates inline api_key persistence; if False,
          inline keys are stripped from the draft before commit so environment
          variables remain the source of truth. This is the safe default.
        - ``allowed_inline_providers`` narrows inline-key persistence to the
          providers the current UI flow explicitly authorized.
        - On save failure, the existing config file is restored from the backup
          and the in-memory ``Config`` state is reloaded from disk.
        """
        report = self.validate()
        if not report.ok:
            return report

        allowed_inline_names = (
            {str(name).strip() for name in allowed_inline_providers if str(name).strip()}
            if allowed_inline_providers is not None
            else None
        )
        if not allow_inline_key or allowed_inline_names is not None:
            for provider in self.llm["providers"]:
                if allow_inline_key and allowed_inline_names is not None and provider.get("name") in allowed_inline_names:
                    continue
                # Strip inline keys entirely; only env references survive.
                provider.pop("api_key", None)
                provider["_api_key_source"] = "env" if provider.get("api_key_env") else "none"

        # Take a snapshot of the live config so we can roll back in memory.
        previous_llm = copy.deepcopy(config.llm)
        previous_extra = copy.deepcopy(config._extra_fields)
        previous_state = {
            "workspace_root": config.workspace_root,
            "skills_dir": config.skills_dir,
            "shell_type": config.shell_type,
            "authorization_level": config.authorization_level,
            "plan_mode": config.plan_mode,
            "thinking_mode": config.thinking_mode,
            "context_management_defaults": copy.deepcopy(config.context_management_defaults),
            "sessions": copy.deepcopy(config.sessions),
            "ui": copy.deepcopy(config.ui),
            "policy": copy.deepcopy(config.policy),
        }

        # Write backup of the on-disk file before mutating live Config.
        backup_path: Optional[Path] = None
        if backup and config.config_path.exists():
            backup_path = Config._write_backup(config.config_path)

        config.llm = copy.deepcopy(self.llm)
        config._extra_fields = copy.deepcopy(self.extra_fields)
        config.workspace_root = self.workspace_root
        config.skills_dir = self.skills_dir
        config.shell_type = self.shell_type
        config.authorization_level = self.authorization_level
        config.plan_mode = self.plan_mode
        config.thinking_mode = self.thinking_mode
        config.context_management_defaults = copy.deepcopy(self.context_management_defaults)
        config.sessions = copy.deepcopy(self.sessions)
        config.ui = copy.deepcopy(self.ui)
        config.policy = copy.deepcopy(self.policy)
        config._sync_runtime_fields()

        try:
            config.save(backup=False)
        except Exception as exc:
            # Roll back: restore prior on-disk file, then reload Config in memory.
            if backup_path and backup_path.exists():
                Config.restore_backup(config.config_path, backup_path)
            config._extra_fields = previous_extra
            config.llm = previous_llm
            config.workspace_root = previous_state["workspace_root"]
            config.skills_dir = previous_state["skills_dir"]
            config.shell_type = previous_state["shell_type"]
            config.authorization_level = previous_state["authorization_level"]
            config.plan_mode = previous_state["plan_mode"]
            config.thinking_mode = previous_state["thinking_mode"]
            config.context_management_defaults = previous_state["context_management_defaults"]
            config.sessions = previous_state["sessions"]
            config.ui = previous_state["ui"]
            config.policy = previous_state["policy"]
            config.load()
            config._sync_runtime_fields()
            report.add_error(f"Failed to save config: {exc}")
            return report

        return report

    # ---- helpers ------------------------------------------------------------------

    def _normalize_context_management(self, value: Any) -> Dict[str, Any]:
        from agent.config import CONTEXT_MANAGEMENT_DEFAULTS

        settings = dict(CONTEXT_MANAGEMENT_DEFAULTS)
        if isinstance(value, dict):
            settings.update({key: value[key] for key in settings if key in value})
        settings["enabled"] = bool(settings["enabled"])
        settings["auto_compress"] = bool(settings["auto_compress"])
        settings["trigger_percent"] = min(100.0, max(1.0, float(settings["trigger_percent"])))
        settings["target_percent"] = min(settings["trigger_percent"], max(1.0, float(settings["target_percent"])))
        settings["preserve_recent_turns"] = max(0, int(settings["preserve_recent_turns"]))
        return settings

    def _ensure_active(self) -> None:
        if not self.llm["providers"]:
            self.llm["active_provider"] = ""
            self.llm["active_model"] = ""
            return
        if not self.llm["active_provider"] or not get_provider(self.llm["providers"], self.llm["active_provider"]):
            self.llm["active_provider"] = self.llm["providers"][0]["name"]
            self.llm["active_model"] = self.llm["providers"][0]["models"][0]["name"]
        provider = get_provider(self.llm["providers"], self.llm["active_provider"])
        if provider and not get_model(provider, self.llm["active_model"]):
            self.llm["active_model"] = provider["models"][0]["name"]
