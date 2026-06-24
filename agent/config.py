import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.config_migration import build_llm_from_legacy
from agent.profile_resolver import (
    get_active_profile as _get_active_profile,
    mask_key,
)
from agent.provider_registry import (
    LLM_DEFAULTS,
    get_model,
    get_provider,
    make_model,
    make_provider,
    merge_model_defaults,
    normalize_model,
    normalize_provider,
    normalize_providers,
    redact_api_key,
    resolve_profile_choice,
)

ACTIVE_LLM_FIELDS = ("api_key", "base_url", "model", "temperature", "max_tokens", "context_window")
BACKUP_GLOB_PREFIX = "config.backup."
SESSION_DEFAULTS = {
    "enabled": True,
    "storage_dir": ".kairo/sessions",
    "autosave": True,
    "save_interval_seconds": 1.0,
    "max_sessions": 200,
}
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
        self._load_error: Optional[Exception] = None
        self._extra_fields: Dict[str, Any] = {}

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
        self.sessions: Dict[str, Any] = dict(SESSION_DEFAULTS)
        self.workspace_root: str = "."
        self.skills_dir: str = "./skills"
        self.shell_type: str = "cmd"
        self.authorization_level: str = "manual"
        self.plan_mode: bool = False
        self.thinking_mode: bool = False
        self.model_roles: Dict[str, str] = {}
        self.workspace_bookmarks: List[Dict[str, str]] = []
        self.llm: Dict[str, Any] = {
            "active_profile": "",
            "active_provider": "",
            "active_model": "",
            "defaults": dict(LLM_DEFAULTS),
            "providers": [],
            "profiles": [],
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

    def _normalize_sessions(self, value: Any) -> Dict[str, Any]:
        settings = dict(SESSION_DEFAULTS)
        if isinstance(value, dict):
            settings.update({key: value[key] for key in settings if key in value})
        settings["enabled"] = bool(settings["enabled"])
        settings["autosave"] = bool(settings["autosave"])
        settings["save_interval_seconds"] = max(0.0, float(settings["save_interval_seconds"]))
        settings["max_sessions"] = max(1, int(settings["max_sessions"]))
        return settings

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

    def _normalize_workspace_bookmarks(self, value: Any) -> List[Dict[str, str]]:
        bookmarks: List[Dict[str, str]] = []
        if not isinstance(value, list):
            return bookmarks
        seen: set = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
            if not name or not path or name.lower() in seen:
                continue
            seen.add(name.lower())
            bookmarks.append({"name": name, "path": path})
        return bookmarks

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

        defaults = self._normalize_llm_defaults(value.get("defaults", fallback_data))

        # New 0.2.5 profile-first structure takes precedence.
        raw_profiles = value.get("profiles", [])
        if isinstance(raw_profiles, list) and raw_profiles:
            llm = {
                "active_profile": str(value.get("active_profile", "")).strip(),
                "active_provider": "",
                "active_model": "",
                "defaults": defaults,
                "providers": [],
                "profiles": list(raw_profiles),
            }
            return llm

        # Legacy provider-centric structure.
        llm = {
            "active_profile": "",
            "active_provider": str(value.get("active_provider", "")).strip(),
            "active_model": str(value.get("active_model", "")).strip(),
            "defaults": defaults,
            "providers": self._normalize_providers(value.get("providers", [])),
            "profiles": [],
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
        if self.llm.get("profiles"):
            return [p.get("label") or p.get("id") or p.get("name", "") for p in self.llm["profiles"]]
        names: List[str] = []
        for provider in self.llm["providers"]:
            for model in provider["models"]:
                names.append(self._format_profile_label(provider["name"], model["name"]))
        return names

    def get_profile_ids(self) -> List[str]:
        """Return profile ids (new structure) or legacy provider/model labels."""
        if self.llm.get("profiles"):
            return [str(p.get("id", "")).strip() for p in self.llm["profiles"] if p.get("id")]
        return self.get_model_profile_names()

    def _build_legacy_profiles(self) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        if self.llm.get("profiles"):
            for profile in self.llm["profiles"]:
                pid = str(profile.get("id", "")).strip()
                provider = str(profile.get("provider", "")).strip() or (pid.split("/", 1)[0] if "/" in pid else pid)
                model = str(profile.get("model", "")).strip() or (pid.split("/", 1)[1] if "/" in pid else pid)
                entry = {
                    "name": str(profile.get("label", "")).strip() or pid,
                    "base_url": str(profile.get("base_url", "")),
                    "model": model,
                    "temperature": float(profile.get("temperature", self.llm["defaults"]["temperature"])),
                    "max_tokens": int(profile.get("max_tokens", self.llm["defaults"]["max_tokens"])),
                    "context_window": int(profile.get("context_window", self.llm["defaults"]["context_window"])),
                }
                if profile.get("api_key"):
                    entry["api_key"] = profile["api_key"]
                if profile.get("api_key_env"):
                    entry["api_key_env"] = profile["api_key_env"]
                if isinstance(profile.get("context_management"), dict):
                    entry["context_management"] = dict(profile["context_management"])
                profiles.append(entry)
            return profiles
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

    def apply_profile(self, profile_id: str) -> bool:
        """Switch active profile by id (new structure) or legacy label."""
        profile_id = (profile_id or "").strip()
        if not profile_id:
            return False
        if self.llm.get("profiles"):
            for profile in self.llm["profiles"]:
                if str(profile.get("id", "")).strip() == profile_id:
                    self.llm["active_profile"] = profile_id
                    self._sync_runtime_fields()
                    return True
                label = str(profile.get("label", "")).strip()
                if label and label == profile_id:
                    self.llm["active_profile"] = str(profile.get("id", "")).strip()
                    self._sync_runtime_fields()
                    return True
            return False
        return self.apply_model_profile(profile_id)

    def get_active_llm_settings(self) -> Dict[str, Any]:
        """Resolve runtime LLM settings using the profile-first resolver."""
        profile = _get_active_profile(self)
        if profile is None:
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

        base_url = self._base_url_override if self._base_url_override is not None else profile.base_url
        api_key = profile.api_key
        api_key_source = profile.api_key_source
        if not api_key and self._global_api_key_override is not None:
            api_key = self._global_api_key_override
            api_key_source = "override"
        model = self._model_override if self._model_override is not None else profile.model

        provider_name = profile.provider
        if not provider_name and "/" in profile.id:
            provider_name = profile.id.split("/", 1)[0]

        return {
            "provider_name": provider_name,
            "model_name": profile.model,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_source": api_key_source,
            "temperature": float(profile.temperature),
            "max_tokens": int(profile.max_tokens),
            "context_window": int(
                self._context_window_override
                if self._context_window_override is not None
                else profile.context_window
            ),
            "context_management": dict(profile.context_management),
            "runtime_model": model,
            "profile_id": profile.id,
            "profile_label": profile.label,
        }

    def _sync_runtime_fields(self):
        settings = self.get_active_llm_settings()
        self.active_provider = settings["provider_name"]
        self.active_model = settings["model_name"]
        self.active_model_profile = self._format_profile_label(self.active_provider, self.active_model) if self.active_provider else settings.get("profile_label", settings["model_name"])
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

    # ---- Runtime editing helpers -------------------------------------------------

    def add_provider(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        models: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Add a new provider. Returns False if the name already exists."""
        name = (name or "").strip()
        if not name or self._get_provider(name):
            return False
        normalized = make_provider(
            name=name,
            base_url=base_url,
            models=models or [],
            api_key=api_key,
            api_key_env=api_key_env,
            normalize_context_management=self._normalize_context_management,
        )
        if not normalized:
            return False
        if not normalized["models"]:
            return False
        self.llm["providers"].append(normalized)
        if not self.llm["active_provider"]:
            self.llm["active_provider"] = normalized["name"]
            self.llm["active_model"] = normalized["models"][0]["name"]
        self._sync_runtime_fields()
        return True

    def update_provider(
        self,
        name: str,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: Optional[str] = None,
    ) -> bool:
        """Update base_url / api_key / api_key_env for an existing provider."""
        provider = self._get_provider(name)
        if not provider:
            return False
        if base_url is not None:
            provider["base_url"] = base_url.strip()
        if api_key is not None:
            provider["api_key"] = api_key
            provider["_api_key_source"] = "file" if api_key else ("env" if provider.get("api_key_env") else "none")
        if api_key_env is not None:
            env_value = api_key_env.strip()
            provider["api_key_env"] = env_value
            if not api_key:
                provider["_api_key_source"] = "env" if env_value else "none"
        self._sync_runtime_fields()
        return True

    def remove_provider(self, name: str) -> bool:
        """Remove a provider and re-select an active profile if needed."""
        provider = self._get_provider(name)
        if not provider:
            return False
        self.llm["providers"] = [p for p in self.llm["providers"] if p["name"] != name]
        if self.llm["active_provider"] == name:
            if self.llm["providers"]:
                self.llm["active_provider"] = self.llm["providers"][0]["name"]
                self.llm["active_model"] = self.llm["providers"][0]["models"][0]["name"]
            else:
                self.llm["active_provider"] = ""
                self.llm["active_model"] = ""
        self._sync_runtime_fields()
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
        provider = self._get_provider(provider_name)
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
        self._sync_runtime_fields()
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
    ) -> bool:
        provider = self._get_provider(provider_name)
        if not provider:
            return False
        model = get_model(provider, model_name)
        if not model:
            return False
        if temperature is not None:
            model["temperature"] = float(temperature)
        if max_tokens is not None:
            model["max_tokens"] = int(max_tokens)
        if context_window is not None:
            model["context_window"] = int(context_window)
        if context_management is not None:
            model["context_management"] = self._normalize_context_management(context_management)
        self._sync_runtime_fields()
        return True

    def remove_model(self, provider_name: str, model_name: str) -> bool:
        provider = self._get_provider(provider_name)
        if not provider:
            return False
        if not get_model(provider, model_name):
            return False
        if len(provider["models"]) <= 1:
            return False
        provider["models"] = [m for m in provider["models"] if m["name"] != model_name]
        if self.llm["active_provider"] == provider_name and self.llm["active_model"] == model_name:
            self.llm["active_model"] = provider["models"][0]["name"]
        self._sync_runtime_fields()
        return True

    def set_active_model(self, provider_name: str, model_name: str) -> bool:
        return self.select_active_model(provider_name, model_name)

    def rename_provider(self, old_name: str, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not old_name or not new_name:
            return False
        provider = self._get_provider(old_name)
        if not provider or (old_name != new_name and self._get_provider(new_name)):
            return False
        provider["name"] = new_name
        if self.llm["active_provider"] == old_name:
            self.llm["active_provider"] = new_name
        self._sync_runtime_fields()
        return True

    def rename_model(self, provider_name: str, old_name: str, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        provider = self._get_provider(provider_name)
        if not provider:
            return False
        model = get_model(provider, old_name)
        if not model or (old_name != new_name and get_model(provider, new_name)):
            return False
        model["name"] = new_name
        if self.llm["active_provider"] == provider_name and self.llm["active_model"] == old_name:
            self.llm["active_model"] = new_name
        self._sync_runtime_fields()
        return True

    # ---- API Key safety ---------------------------------------------------------

    @staticmethod
    def redact_api_key(value: str) -> str:
        return redact_api_key(value)

    def describe_active_api_key(self) -> str:
        """Return a human description of the active API key provenance (no raw key)."""
        profile = _get_active_profile(self)
        if profile is None:
            return "API Key: missing"
        if profile.api_key_source == "env":
            return "API Key: env"
        if profile.api_key_source == "override":
            return "API Key: runtime override (env OPENAI_API_KEY/OPENAI_BASE_URL)"
        if profile.api_key_source == "file":
            return f"API Key: inline in config.json [warning] preview={mask_key(profile.api_key)}"
        return "API Key: missing"

    # ---- Backup helper ----------------------------------------------------------

    @staticmethod
    def list_backups(config_path: os.PathLike | str) -> List[Dict[str, Any]]:
        """Return backup files for the given config path sorted newest-first."""
        path = Path(config_path)
        if not path.exists():
            return []
        backups: List[Dict[str, Any]] = []
        for entry in path.parent.glob(f"{BACKUP_GLOB_PREFIX}*{path.suffix or '.json'}"):
            if not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            backups.append({
                "name": entry.name,
                "path": str(entry),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        backups.sort(key=lambda item: item.get("modified", 0), reverse=True)
        return backups

    def load(self):
        """Load configuration from JSON and environment variables."""
        data: Dict[str, Any] = {}
        self._load_error = None
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle) or {}
            except Exception as exc:
                self._load_error = exc
                print(f"[Warning] Failed to load config from {self.config_path}: {exc}")
                data = {}

        # Preserve truly unknown top-level fields so we do not silently drop user
        # extensions. Legacy fields that have been migrated to the new llm block are
        # intentionally excluded from extra preservation.
        known_keys = {
            "llm", "context_management", "ui", "sessions", "workspace_root",
            "skills_dir", "shell_type", "authorization_level", "auto_mode",
            "plan_mode", "thinking_mode", "policy", "model_roles", "workspace_bookmarks",
            # Legacy fields consumed during migration; must not be written back.
            "api_key", "base_url", "model", "models", "active_provider",
            "active_model", "active_model_profile", "model_profiles",
            "profile_defaults", "temperature", "max_tokens", "context_window",
        }
        self._extra_fields = {key: value for key, value in data.items() if key not in known_keys}

        self.model_roles = {str(k).strip(): str(v).strip() for k, v in data.get("model_roles", {}).items() if str(k).strip() and str(v).strip()}
        self.workspace_bookmarks = self._normalize_workspace_bookmarks(data.get("workspace_bookmarks", []))

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
        self.sessions = self._normalize_sessions(data.get("sessions", self.sessions))

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

    def save(self, *, backup: bool = False):
        """Save configuration using the provider-centric llm structure.

        ``backup=True`` writes a timestamped copy of the existing on-disk file to
        ``config.backup.YYYYMMDD-HHMMSS.json`` before the atomic replace. Config
        backups never persist raw API keys; this method relies on the existing
        file content so leaked keys in an existing file would be preserved in
        the backup — callers should ensure env-based key storage in production.
        """
        if self._load_error is not None:
            raise RuntimeError(
                f"Config at {self.config_path} could not be loaded ({self._load_error}); "
                "fix or rebuild it before saving."
            ) from self._load_error

        if backup and self.config_path.exists():
            self._write_backup(self.config_path)

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

        data: Dict[str, Any] = {
            "llm": {
                "active_provider": self.llm["active_provider"],
                "active_model": self.llm["active_model"],
                "defaults": dict(self.llm["defaults"]),
                "providers": providers,
            },
            "context_management": dict(self.context_management_defaults),
            "ui": dict(self.ui),
            "sessions": dict(self.sessions),
            "workspace_root": self.workspace_root,
            "skills_dir": self.skills_dir,
            "shell_type": self.shell_type,
            "authorization_level": self.authorization_level,
            "plan_mode": self.plan_mode,
            "thinking_mode": self.thinking_mode,
            "policy": copy.deepcopy(self.policy),
            "workspace_bookmarks": list(self.workspace_bookmarks),
        }
        # Restore user-defined extension fields so they are not silently dropped.
        for key, value in self._extra_fields.items():
            if key not in data:
                data[key] = value

        # 0.2.5: persist new profile-first structure when profiles are configured.
        if self.llm.get("profiles"):
            data["llm"] = {
                "active_profile": self.llm.get("active_profile", ""),
                "defaults": dict(self.llm["defaults"]),
                "profiles": self._serialize_profiles(),
            }
            data["model_roles"] = dict(self.model_roles)
        else:
            data["llm"] = self._serialize_legacy_llm()

        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.config_path)
        except Exception as exc:
            print(f"[Error] Failed to save config to {self.config_path}: {exc}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"Config(model='{self.model}', base_url='{self.base_url}', shell='{self.shell_type}', "
            f"context_window={self.context_window}, auto={self.auto_mode}, "
            f"plan={self.plan_mode}, think={self.thinking_mode})"
        )

    # ---- Serialization helpers --------------------------------------------------

    def _serialize_profiles(self) -> List[Dict[str, Any]]:
        """Serialize llm.profiles for disk, preserving inline keys by default."""
        profiles: List[Dict[str, Any]] = []
        for profile in self.llm.get("profiles", []):
            serialized: Dict[str, Any] = {
                "id": str(profile.get("id", "")).strip(),
                "label": str(profile.get("label", "")).strip(),
                "provider": str(profile.get("provider", "")).strip(),
                "base_url": str(profile.get("base_url", "")).strip(),
                "model": str(profile.get("model", "")).strip(),
                "api_key": str(profile.get("api_key", "")),
                "api_key_env": str(profile.get("api_key_env", "")).strip(),
                "temperature": float(profile.get("temperature", self.llm["defaults"]["temperature"])),
                "max_tokens": int(profile.get("max_tokens", self.llm["defaults"]["max_tokens"])),
                "context_window": int(profile.get("context_window", self.llm["defaults"]["context_window"])),
            }
            if isinstance(profile.get("context_management"), dict):
                serialized["context_management"] = dict(profile["context_management"])
            profiles.append(serialized)
        return profiles

    def _serialize_legacy_llm(self) -> Dict[str, Any]:
        """Serialize the legacy provider-centric llm structure."""
        providers: List[Dict[str, Any]] = []
        for provider in self.llm["providers"]:
            serialized_provider = {
                "name": provider["name"],
                "base_url": provider.get("base_url", ""),
                "models": [],
            }
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
        return {
            "active_provider": self.llm["active_provider"],
            "active_model": self.llm["active_model"],
            "defaults": dict(self.llm["defaults"]),
            "providers": providers,
        }

    # ---- Backup machinery --------------------------------------------------------

    @staticmethod
    def _write_backup(config_path: Path) -> Optional[Path]:
        """Copy ``config_path`` to a timestamped backup in the same directory."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = config_path.suffix or ".json"
        backup_path = config_path.with_name(f"{BACKUP_GLOB_PREFIX}{timestamp}{suffix}")
        try:
            with open(config_path, "r", encoding="utf-8") as source:
                content = source.read()
            with open(backup_path, "w", encoding="utf-8") as dest:
                dest.write(content)
            return backup_path
        except Exception:
            try:
                backup_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    @classmethod
    def create_backup(cls, config_path: os.PathLike | str) -> Optional[Path]:
        """Create an explicit backup of *config_path* and return its location."""
        path = Path(config_path)
        if not path.exists():
            return None
        return cls._write_backup(path)

    @classmethod
    def restore_backup(cls, config_path: os.PathLike | str, backup_name: str) -> bool:
        """Copy backup ``backup_name`` back over ``config_path`` atomically.

        ``backup_name`` may be either a bare filename or an absolute path inside
        the config directory. The original file is overwritten via temp+replace
        so a partial write never leaves the config empty.
        """
        config_path = Path(config_path)
        backup_path = Path(backup_name)
        if not backup_path.is_absolute():
            backup_path = config_path.parent / backup_path
        if not backup_path.exists():
            return False
        try:
            with open(backup_path, "r", encoding="utf-8") as source:
                content = source.read()
            tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as dest:
                dest.write(content)
                dest.flush()
                os.fsync(dest.fileno())
            os.replace(tmp_path, config_path)
            return True
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False
