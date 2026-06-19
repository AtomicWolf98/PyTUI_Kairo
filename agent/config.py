import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.config_migration import build_llm_from_legacy
from agent.provider_registry import (
    LLM_DEFAULTS,
    get_model,
    get_provider,
    normalize_model,
    normalize_provider,
    normalize_providers,
    resolve_profile_choice,
)

ACTIVE_LLM_FIELDS = ("api_key", "base_url", "model", "temperature", "max_tokens", "context_window")
CONTEXT_MANAGEMENT_DEFAULTS = {
    "enabled": True,
    "auto_compress": True,
    "trigger_percent": 85.0,
    "target_percent": 60.0,
    "preserve_recent_turns": 4,
}
UI_DEFAULTS = {
    "mode": "auto",
    "theme": "kairo-dark",
    "animation": "full",
    "mascot": True,
    "dock_breakpoint": 120,
    "dock_width_ratio": 0.333,
    "dock_min_width": 36,
    "dock_max_width": 64,
    "reduced_motion": False,
    "workspace_enabled": True,
    "workspace_refresh_seconds": 2.0,
    "workspace_max_files": 2000,
    "workspace_diff_max_bytes": 204800,
}


class Config:
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self._global_api_key_override: Optional[str] = None
        self._base_url_override: Optional[str] = None
        self._model_override: Optional[str] = None
        self._context_window_override: Optional[int] = None

        self.api_key: str = ""
        self.base_url: str = "https://api.openai.com/v1"
        self.model: str = "gpt-4o"
        self.models: List[str] = []
        self.active_provider: str = ""
        self.active_model: str = ""
        self.active_model_profile: str = ""
        self.model_profiles: List[Dict[str, Any]] = []
        self.profile_defaults: Dict[str, Any] = dict(LLM_DEFAULTS)
        self.temperature: float = float(LLM_DEFAULTS["temperature"])
        self.max_tokens: int = int(LLM_DEFAULTS["max_tokens"])
        self.context_window: int = int(LLM_DEFAULTS["context_window"])
        self.context_management_defaults: Dict[str, Any] = dict(CONTEXT_MANAGEMENT_DEFAULTS)
        self.context_management: Dict[str, Any] = dict(CONTEXT_MANAGEMENT_DEFAULTS)
        self.ui: Dict[str, Any] = dict(UI_DEFAULTS)
        self.workspace_root: str = "."
        self.skills_dir: str = "./skills"
        self.shell_type: str = "cmd"
        self.authorization_level: str = "manual"
        self.plan_mode: bool = False
        self.thinking_mode: bool = False
        self.llm: Dict[str, Any] = {
            "active_provider": "",
            "active_model": "",
            "defaults": dict(LLM_DEFAULTS),
            "providers": [],
        }
        self.policy: Dict[str, Any] = {
            "workspace_path": {
                "allow_absolute_outside": False,
            },
            "network": {
                "allow_hosts": [],
                "deny_hosts": [],
                "deny_private_loopback": True,
            },
            "command": {
                "allow_patterns": [],
                "deny_patterns": [],
                "require_confirmation_for_chained": True,
            },
            "python": {
                "deny_builtins": ["exec", "eval", "compile", "__import__", "open"],
                "deny_modules": ["os", "subprocess", "sys", "socket", "urllib"],
            },
            "skills": {
                "require_hash": False,
            },
            "resource_limits": {
                "max_read_bytes": 1_048_576,
                "max_search_bytes": 1_048_576,
                "max_fetch_bytes": 1_048_576,
                "max_search_depth": 10,
                "max_search_results": 100,
            },
        }

        self.load()

    @property
    def auto_mode(self) -> bool:
        """Backward-compatible alias for authorization_level == 'auto' or 'yolo'."""
        return self.authorization_level in ("auto", "yolo")

    @auto_mode.setter
    def auto_mode(self, value: bool):
        """Backward-compatible setter: True -> 'auto', False -> 'manual'."""
        self.authorization_level = "auto" if value else "manual"

    def _format_profile_label(self, provider_name: str, model_name: str) -> str:
        return f"{provider_name} / {model_name}"

    def _normalize_context_management(self, value: Any) -> Dict[str, Any]:
        settings = dict(CONTEXT_MANAGEMENT_DEFAULTS)
        if isinstance(value, dict):
            settings.update({key: value[key] for key in settings if key in value})
        settings["enabled"] = bool(settings["enabled"])
        settings["auto_compress"] = bool(settings["auto_compress"])
        settings["trigger_percent"] = min(100.0, max(1.0, float(settings["trigger_percent"])))
        settings["target_percent"] = min(
            settings["trigger_percent"],
            max(1.0, float(settings["target_percent"])),
        )
        settings["preserve_recent_turns"] = max(0, int(settings["preserve_recent_turns"]))
        return settings

    def _normalize_llm_defaults(self, value: Any) -> Dict[str, Any]:
        defaults = dict(LLM_DEFAULTS)
        if isinstance(value, dict):
            for key in defaults:
                if key in value:
                    defaults[key] = value[key]
        defaults["temperature"] = float(defaults["temperature"])
        defaults["max_tokens"] = int(defaults["max_tokens"])
        defaults["context_window"] = int(defaults["context_window"])
        return defaults

    def _normalize_model(self, value: Any) -> Optional[Dict[str, Any]]:
        return normalize_model(value, self._normalize_context_management)

    def _normalize_provider(self, value: Any) -> Optional[Dict[str, Any]]:
        return normalize_provider(value, self._normalize_context_management)

    def _normalize_providers(self, value: Any) -> List[Dict[str, Any]]:
        return normalize_providers(value, self._normalize_context_management)

    def _build_llm_from_legacy(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return build_llm_from_legacy(
            data,
            self._normalize_context_management,
            self._normalize_llm_defaults,
            str(data.get("base_url", self.base_url)).strip(),
            self.model,
        )

    def _normalize_llm_config(self, value: Any, fallback_data: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return self._build_llm_from_legacy(fallback_data)

        llm = {
            "active_provider": str(value.get("active_provider", "")).strip(),
            "active_model": str(value.get("active_model", "")).strip(),
            "defaults": self._normalize_llm_defaults(value.get("defaults", fallback_data)),
            "providers": self._normalize_providers(value.get("providers", [])),
        }
        if not llm["providers"]:
            return self._build_llm_from_legacy(fallback_data)
        if not llm["active_provider"]:
            llm["active_provider"] = llm["providers"][0]["name"]
        active_provider = next(
            (provider for provider in llm["providers"] if provider["name"] == llm["active_provider"]),
            llm["providers"][0],
        )
        llm["active_provider"] = active_provider["name"]
        if not llm["active_model"]:
            llm["active_model"] = active_provider["models"][0]["name"]
        if not any(model["name"] == llm["active_model"] for model in active_provider["models"]):
            llm["active_model"] = active_provider["models"][0]["name"]
        return llm

    def _get_provider(self, provider_name: str) -> Optional[Dict[str, Any]]:
        return get_provider(self.llm["providers"], provider_name)

    def _get_model(self, provider_name: str, model_name: str) -> Optional[Dict[str, Any]]:
        provider = self._get_provider(provider_name)
        if not provider:
            return None
        return get_model(provider, model_name)

    def _resolve_profile_choice(self, choice: str) -> Optional[Tuple[str, str]]:
        return resolve_profile_choice(choice, self.llm["providers"])

    def get_provider_names(self) -> List[str]:
        return [provider["name"] for provider in self.llm["providers"]]

    def get_model_names(self, provider_name: str) -> List[str]:
        provider = self._get_provider(provider_name)
        if not provider:
            return []
        return [model["name"] for model in provider["models"]]

    def get_model_profile_names(self) -> List[str]:
        names: List[str] = []
        for provider in self.llm["providers"]:
            for model in provider["models"]:
                names.append(self._format_profile_label(provider["name"], model["name"]))
        return names

    def _build_legacy_profiles(self) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        for provider in self.llm["providers"]:
            for model in provider["models"]:
                profile_name = str(model.get("legacy_profile_name") or self._format_profile_label(provider["name"], model["name"]))
                profile = {
                    "name": profile_name,
                    "base_url": provider.get("base_url", ""),
                    "model": model["name"],
                    "temperature": float(model.get("temperature", self.llm["defaults"]["temperature"])),
                    "max_tokens": int(model.get("max_tokens", self.llm["defaults"]["max_tokens"])),
                    "context_window": int(model.get("context_window", self.llm["defaults"]["context_window"])),
                }
                if provider.get("api_key"):
                    profile["api_key"] = provider["api_key"]
                if provider.get("api_key_env"):
                    profile["api_key_env"] = provider["api_key_env"]
                if isinstance(model.get("context_management"), dict):
                    profile["context_management"] = dict(model["context_management"])
                profiles.append(profile)
        return profiles

    def get_active_llm_settings(self) -> Dict[str, Any]:
        provider = self._get_provider(self.llm["active_provider"])
        if not provider and self.llm["providers"]:
            provider = self.llm["providers"][0]
        if not provider:
            return {
                "provider_name": "",
                "model_name": self.model,
                "base_url": self.base_url,
                "api_key": self.api_key,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "context_window": self.context_window,
                "context_management": dict(self.context_management_defaults),
            }

        model = self._get_model(provider["name"], self.llm["active_model"])
        if not model:
            model = provider["models"][0]

        context_management = dict(self.context_management_defaults)
        if isinstance(model.get("context_management"), dict):
            context_management.update(model["context_management"])
        context_management = self._normalize_context_management(context_management)

        api_key_env = str(provider.get("api_key_env", "")).strip()
        api_key = ""
        api_key_source = provider.get("_api_key_source", "none")
        if api_key_env and os.environ.get(api_key_env):
            api_key = os.environ[api_key_env]
            api_key_source = "env"
        elif self._global_api_key_override is not None:
            api_key = self._global_api_key_override
            api_key_source = "override"
        else:
            api_key = str(provider.get("api_key", ""))

        return {
            "provider_name": provider["name"],
            "model_name": model["name"],
            "base_url": self._base_url_override if self._base_url_override is not None else provider.get("base_url", self.base_url),
            "api_key": api_key,
            "api_key_source": api_key_source,
            "temperature": float(model.get("temperature", self.llm["defaults"]["temperature"])),
            "max_tokens": int(model.get("max_tokens", self.llm["defaults"]["max_tokens"])),
            "context_window": int(
                self._context_window_override
                if self._context_window_override is not None
                else model.get("context_window", self.llm["defaults"]["context_window"])
            ),
            "context_management": context_management,
            "runtime_model": self._model_override if self._model_override is not None else model["name"],
        }

    def _sync_runtime_fields(self):
        settings = self.get_active_llm_settings()
        self.active_provider = settings["provider_name"]
        self.active_model = settings["model_name"]
        self.active_model_profile = self._format_profile_label(self.active_provider, self.active_model) if self.active_provider else ""
        self.api_key = str(settings["api_key"])
        self.base_url = str(settings["base_url"])
        self.model = str(settings["runtime_model"])
        self.temperature = float(settings["temperature"])
        self.max_tokens = int(settings["max_tokens"])
        self.context_window = int(settings["context_window"])
        self.context_management = dict(settings["context_management"])
        self.models = self.get_model_profile_names() or [self.model]
        self.model_profiles = self._build_legacy_profiles()
        self.profile_defaults = {
            "temperature": self.llm["defaults"]["temperature"],
            "max_tokens": self.llm["defaults"]["max_tokens"],
            "context_window": self.llm["defaults"]["context_window"],
        }

    def select_active_model(self, provider_name: str, model_name: str) -> bool:
        if not self._get_model(provider_name, model_name):
            return False
        self.llm["active_provider"] = provider_name
        self.llm["active_model"] = model_name
        self._sync_runtime_fields()
        return True

    def apply_model_profile(self, profile_name: str) -> bool:
        choice = self._resolve_profile_choice(profile_name)
        if not choice:
            return False
        return self.select_active_model(*choice)

    def load(self):
        """Load configuration from JSON and environment variables."""
        data: Dict[str, Any] = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle) or {}
            except Exception as exc:
                print(f"[Warning] Failed to load config from {self.config_path}: {exc}")

        self.workspace_root = str(data.get("workspace_root", self.workspace_root))
        self.skills_dir = str(data.get("skills_dir", self.skills_dir))
        self.shell_type = str(data.get("shell_type", self.shell_type))
        # Backward compatibility: legacy auto_mode bool maps to authorization_level.
        legacy_auto_mode = data.get("auto_mode")
        configured_level = data.get("authorization_level")
        if configured_level in ("manual", "auto", "yolo"):
            self.authorization_level = configured_level
        elif isinstance(legacy_auto_mode, bool):
            self.authorization_level = "auto" if legacy_auto_mode else "manual"
        self.plan_mode = bool(data.get("plan_mode", self.plan_mode))
        self.thinking_mode = bool(data.get("thinking_mode", self.thinking_mode))
        self.context_management_defaults = self._normalize_context_management(
            data.get("context_management", self.context_management_defaults)
        )
        configured_ui = data.get("ui", {})
        if isinstance(configured_ui, dict):
            self.ui.update({key: configured_ui[key] for key in UI_DEFAULTS if key in configured_ui})
            if "dock_max_width" not in configured_ui and "dock_width" in configured_ui:
                legacy_width = int(configured_ui["dock_width"])
                self.ui["dock_max_width"] = 64 if legacy_width in (32, 42) else legacy_width
        self.ui["dock_breakpoint"] = max(60, int(self.ui["dock_breakpoint"]))
        self.ui["dock_width_ratio"] = min(0.5, max(0.2, float(self.ui["dock_width_ratio"])))
        self.ui["dock_min_width"] = max(30, int(self.ui["dock_min_width"]))
        self.ui["dock_max_width"] = max(self.ui["dock_min_width"], int(self.ui["dock_max_width"]))
        self.ui["workspace_enabled"] = bool(self.ui["workspace_enabled"])
        self.ui["workspace_refresh_seconds"] = max(0.5, float(self.ui["workspace_refresh_seconds"]))
        self.ui["workspace_max_files"] = max(1, int(self.ui["workspace_max_files"]))
        self.ui["workspace_diff_max_bytes"] = max(1024, int(self.ui["workspace_diff_max_bytes"]))

        configured_policy = data.get("policy", {})
        if isinstance(configured_policy, dict):
            for section in self.policy:
                section_data = configured_policy.get(section)
                if isinstance(section_data, dict):
                    self.policy[section].update(section_data)

        self.llm = self._normalize_llm_config(data.get("llm"), data)
        self._global_api_key_override = os.environ.get("OPENAI_API_KEY", os.environ.get("GEMINI_API_KEY"))
        self._base_url_override = os.environ.get("OPENAI_BASE_URL")
        self._model_override = os.environ.get("LLM_MODEL")
        context_window_override = os.environ.get("CONTEXT_WINDOW")
        self._context_window_override = int(context_window_override) if context_window_override is not None else None
        self.shell_type = os.environ.get("SHELL_TYPE", self.shell_type)
        self._sync_runtime_fields()

    def save(self):
        """Save configuration using the provider-centric llm structure."""
        providers: List[Dict[str, Any]] = []
        for provider in self.llm["providers"]:
            serialized_provider = {
                "name": provider["name"],
                "base_url": provider.get("base_url", ""),
                "models": [],
            }
            # Only write API keys that originated from the config file itself.
            # Keys provided via environment variables or runtime overrides must
            # never be persisted back to disk.
            key_source = provider.get("_api_key_source", "none")
            if provider.get("api_key") and key_source == "file":
                serialized_provider["api_key"] = provider["api_key"]
            if provider.get("api_key_env"):
                serialized_provider["api_key_env"] = provider["api_key_env"]
            for model in provider["models"]:
                serialized_model = {
                    "name": model["name"],
                    "temperature": float(model.get("temperature", self.llm["defaults"]["temperature"])),
                    "max_tokens": int(model.get("max_tokens", self.llm["defaults"]["max_tokens"])),
                    "context_window": int(model.get("context_window", self.llm["defaults"]["context_window"])),
                }
                if isinstance(model.get("context_management"), dict):
                    serialized_model["context_management"] = dict(model["context_management"])
                serialized_provider["models"].append(serialized_model)
            providers.append(serialized_provider)

        data = {
            "llm": {
                "active_provider": self.llm["active_provider"],
                "active_model": self.llm["active_model"],
                "defaults": dict(self.llm["defaults"]),
                "providers": providers,
            },
            "context_management": dict(self.context_management_defaults),
            "ui": dict(self.ui),
            "workspace_root": self.workspace_root,
            "skills_dir": self.skills_dir,
            "shell_type": self.shell_type,
            "authorization_level": self.authorization_level,
            "plan_mode": self.plan_mode,
            "thinking_mode": self.thinking_mode,
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
        except Exception as exc:
            print(f"[Error] Failed to save config to {self.config_path}: {exc}")

    def __repr__(self) -> str:
        return (
            f"Config(model='{self.model}', base_url='{self.base_url}', shell='{self.shell_type}', "
            f"context_window={self.context_window}, auto={self.auto_mode}, "
            f"plan={self.plan_mode}, think={self.thinking_mode})"
        )
