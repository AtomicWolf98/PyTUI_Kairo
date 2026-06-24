"""Runtime configuration editing for Kairo 0.2.5.

``ConfigDraft`` provides an in-memory mutable copy of the configuration that
can be validated and committed back to disk atomically. 0.2.5 stores API keys
inline in config.json by default; ``export_config`` redacts them unless the
caller explicitly requests keys.

The editor layer is intentionally UI-agnostic; plain prompts and Textual
modals both build a draft, mutate it, and call :meth:`ConfigDraft.apply_to`.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent.config import Config
from agent.profile_resolver import mask_key
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
        self.model_roles: Dict[str, str] = copy.deepcopy(getattr(source, "model_roles", {}))
        self.workspace_bookmarks: List[Dict[str, str]] = copy.deepcopy(getattr(source, "workspace_bookmarks", []))
        self.extra_fields: Dict[str, Any] = copy.deepcopy(source._extra_fields)

    # ---- snapshot ----------------------------------------------------------------

    @classmethod
    def from_config(cls, source: Config) -> "ConfigDraft":
        return cls(source)

    # ---- profile-first mutations (0.2.5) ----------------------------------------

    def _ensure_profiles(self) -> None:
        """Convert legacy providers into profiles when operating in profile mode."""
        if self.llm.get("profiles"):
            return
        profiles: List[Dict[str, Any]] = []
        defaults = self.llm.get("defaults", {})
        for provider in self.llm.get("providers", []):
            pname = str(provider.get("name", "")).strip()
            for model in provider.get("models", []):
                mname = str(model.get("name", "")).strip()
                pid = f"{pname}/{mname}" if pname and mname else (pname or mname)
                profiles.append({
                    "id": pid,
                    "label": "",
                    "provider": pname,
                    "base_url": str(provider.get("base_url", "")).strip(),
                    "api_key": str(provider.get("api_key", "")),
                    "api_key_env": str(provider.get("api_key_env", "")).strip(),
                    "model": mname,
                    "temperature": float(model.get("temperature", defaults.get("temperature", 0.2))),
                    "max_tokens": int(model.get("max_tokens", defaults.get("max_tokens", 4000))),
                    "context_window": int(model.get("context_window", defaults.get("context_window", 128000))),
                    "context_management": self._normalize_context_management(model.get("context_management")),
                })
        active_profile = ""
        if self.llm.get("active_provider") and self.llm.get("active_model"):
            active_profile = f"{self.llm['active_provider']}/{self.llm['active_model']}"
        self.llm = {
            "active_profile": active_profile,
            "active_provider": "",
            "active_model": "",
            "defaults": dict(defaults),
            "providers": [],
            "profiles": profiles,
        }

    def _get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_profiles()
        for profile in self.llm.get("profiles", []):
            if str(profile.get("id", "")).strip() == profile_id:
                return profile
        return None

    def add_profile(
        self,
        *,
        id: str,
        label: str = "",
        provider: str = "",
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        context_window: int = 128000,
        context_management: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self._ensure_profiles()
        pid = (id or "").strip()
        if not pid or self._get_profile(pid):
            return False
        profile = {
            "id": pid,
            "label": (label or "").strip(),
            "provider": (provider or "").strip(),
            "base_url": base_url.strip(),
            "api_key": api_key,
            "api_key_env": api_key_env.strip(),
            "model": (model or pid).strip(),
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "context_window": int(context_window),
            "context_management": self._normalize_context_management(context_management),
        }
        self.llm.setdefault("profiles", []).append(profile)
        if not self.llm.get("active_profile"):
            self.llm["active_profile"] = pid
        return True

    def update_profile(
        self,
        profile_id: str,
        *,
        label: Optional[str] = None,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
        context_management: Optional[Dict[str, Any]] = None,
        new_id: Optional[str] = None,
    ) -> bool:
        profile = self._get_profile(profile_id)
        if not profile:
            return False
        if new_id is not None:
            new_id = new_id.strip()
            if new_id and new_id != profile_id and not self._get_profile(new_id):
                profile["id"] = new_id
                if self.llm.get("active_profile") == profile_id:
                    self.llm["active_profile"] = new_id
        if label is not None:
            profile["label"] = label.strip()
        if provider is not None:
            profile["provider"] = provider.strip()
        if base_url is not None:
            profile["base_url"] = base_url.strip()
        if api_key is not None:
            profile["api_key"] = api_key
        if api_key_env is not None:
            profile["api_key_env"] = api_key_env.strip()
        if model is not None:
            profile["model"] = (model or profile["id"]).strip()
        if temperature is not None:
            profile["temperature"] = float(temperature)
        if max_tokens is not None:
            profile["max_tokens"] = int(max_tokens)
        if context_window is not None:
            profile["context_window"] = int(context_window)
        if context_management is not None:
            profile["context_management"] = self._normalize_context_management(context_management)
        return True

    def remove_profile(self, profile_id: str) -> bool:
        profile = self._get_profile(profile_id)
        if not profile:
            return False
        self.llm["profiles"] = [p for p in self.llm.get("profiles", []) if str(p.get("id", "")).strip() != profile_id]
        if self.llm.get("active_profile") == profile_id:
            self.llm["active_profile"] = self.llm["profiles"][0]["id"] if self.llm["profiles"] else ""
        # Clear role mappings that pointed to this profile.
        self.model_roles = {k: v for k, v in self.model_roles.items() if v != profile_id}
        return True

    def copy_profile(self, source_id: str, new_id: str) -> bool:
        source = self._get_profile(source_id)
        if not source:
            return False
        new_id = (new_id or "").strip()
        if not new_id or self._get_profile(new_id):
            return False
        profile = copy.deepcopy(source)
        profile["id"] = new_id
        profile["label"] = f"Copy of {source.get('label') or source_id}"
        self.llm.setdefault("profiles", []).append(profile)
        return True

    def set_active_profile(self, profile_id: str) -> bool:
        self._ensure_profiles()
        if not self._get_profile(profile_id):
            return False
        self.llm["active_profile"] = profile_id
        return True

    # ---- key management ----------------------------------------------------------

    def set_key(self, profile_id: str, key: str) -> bool:
        return self.update_profile(profile_id, api_key=key)

    def clear_key(self, profile_id: str) -> bool:
        return self.update_profile(profile_id, api_key="")

    def migrate_keys(self) -> List[str]:
        """Migrate legacy provider inline keys into profile inline keys.

        Returns a list of migrated profile ids.
        """
        self._ensure_profiles()
        migrated: List[str] = []
        legacy_providers = {p["name"]: p for p in self._source.llm.get("providers", [])}
        for profile in self.llm.get("profiles", []):
            provider_name = profile.get("provider") or (profile["id"].split("/", 1)[0] if "/" in profile["id"] else "")
            provider = legacy_providers.get(provider_name)
            if not provider:
                continue
            if profile.get("api_key"):
                continue
            legacy_key = str(provider.get("api_key", "")).strip()
            if legacy_key:
                profile["api_key"] = legacy_key
                migrated.append(profile["id"])
        return migrated

    # ---- role management ---------------------------------------------------------

    def set_role(self, role: str, profile_id: str) -> bool:
        self._ensure_profiles()
        if not self._get_profile(profile_id):
            return False
        self.model_roles[str(role).strip()] = str(profile_id).strip()
        return True

    def clear_role(self, role: str) -> bool:
        role = str(role).strip()
        if role not in self.model_roles:
            return False
        del self.model_roles[role]
        return True

    def list_roles(self) -> Dict[str, str]:
        return dict(self.model_roles)

    # ---- workspace bookmarks -----------------------------------------------------

    def add_workspace_bookmark(self, name: str, path: str) -> bool:
        name = (name or "").strip()
        path = (path or "").strip()
        if not name or not path:
            return False
        existing = {b["name"].lower(): b for b in self.workspace_bookmarks}
        existing[name.lower()] = {"name": name, "path": path}
        self.workspace_bookmarks = list(existing.values())
        return True

    def remove_workspace_bookmark(self, name: str) -> bool:
        name = (name or "").strip().lower()
        if not name:
            return False
        original = len(self.workspace_bookmarks)
        self.workspace_bookmarks = [b for b in self.workspace_bookmarks if b["name"].lower() != name]
        return len(self.workspace_bookmarks) < original

    def get_workspace_bookmark(self, name: str) -> Optional[Dict[str, str]]:
        name = (name or "").strip().lower()
        for bookmark in self.workspace_bookmarks:
            if bookmark["name"].lower() == name:
                return dict(bookmark)
        return None

    # ---- provider/model mutations (legacy compatibility) -------------------------

    def add_provider(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        api_key_env: str = "",
        models: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        if self.llm.get("profiles"):
            models = models or []
            first_model = models[0]["name"] if models else name
            return self.add_profile(
                id=f"{name}/{first_model}",
                provider=name,
                base_url=base_url,
                api_key=api_key,
                api_key_env=api_key_env,
                model=first_model,
                temperature=models[0].get("temperature", self.llm["defaults"]["temperature"]) if models else self.llm["defaults"]["temperature"],
                max_tokens=models[0].get("max_tokens", self.llm["defaults"]["max_tokens"]) if models else self.llm["defaults"]["max_tokens"],
                context_window=models[0].get("context_window", self.llm["defaults"]["context_window"]) if models else self.llm["defaults"]["context_window"],
            )
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
        self._ensure_active_legacy()
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
        if self.llm.get("profiles"):
            for profile in self.llm["profiles"]:
                if profile.get("provider") == name:
                    self.update_profile(
                        profile["id"],
                        base_url=base_url,
                        api_key=api_key,
                        api_key_env=api_key_env,
                        provider=rename if rename else None,
                    )
            return True
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
        if self.llm.get("profiles"):
            removed = False
            for profile in list(self.llm["profiles"]):
                if profile.get("provider") == name:
                    self.remove_profile(profile["id"])
                    removed = True
            return removed
        provider = get_provider(self.llm["providers"], name)
        if not provider:
            return False
        self.llm["providers"] = [p for p in self.llm["providers"] if p["name"] != name]
        if self.llm["active_provider"] == name:
            self._ensure_active_legacy()
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
        if self.llm.get("profiles"):
            return self.add_profile(
                id=f"{provider_name}/{name}",
                provider=provider_name,
                base_url="",
                model=name,
                temperature=temperature,
                max_tokens=max_tokens,
                context_window=context_window,
                context_management=context_management,
            )
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
        if self.llm.get("profiles"):
            pid = f"{provider_name}/{model_name}"
            profile = self._get_profile(pid)
            if not profile:
                return False
            kwargs: Dict[str, Any] = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            if context_window is not None:
                kwargs["context_window"] = context_window
            if context_management is not None:
                kwargs["context_management"] = context_management
            if rename is not None:
                kwargs["new_id"] = f"{provider_name}/{rename}"
                kwargs["model"] = rename
            return self.update_profile(pid, **kwargs)
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
        if self.llm.get("profiles"):
            return self.remove_profile(f"{provider_name}/{model_name}")
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
        if self.llm.get("profiles"):
            return self.set_active_profile(f"{provider_name}/{model_name}")
        provider = get_provider(self.llm["providers"], provider_name)
        if not provider or not get_model(provider, model_name):
            return False
        self.llm["active_provider"] = provider_name
        self.llm["active_model"] = model_name
        return True

    def _ensure_active_legacy(self) -> None:
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

    # ---- validation ---------------------------------------------------------------

    def validate(self) -> ValidationReport:
        report = ValidationReport()

        # Profile-first validation.
        if self.llm.get("profiles"):
            self._validate_profiles(report)
        else:
            self._validate_legacy_providers(report)

        # Role validation.
        seen_roles: set = set()
        profile_ids = {str(p.get("id", "")).strip() for p in self.llm.get("profiles", [])}
        provider_model_labels = set(self._source.get_model_profile_names())
        valid_targets = profile_ids | provider_model_labels
        for role, target in self.model_roles.items():
            if role in seen_roles:
                report.add_error(f"Duplicate role mapping for '{role}'.")
            seen_roles.add(role)
            if target not in valid_targets:
                report.add_warning(f"Role '{role}' maps to unknown profile '{target}'.")

        # Workspace bookmarks validation.
        seen_bookmarks: set = set()
        for bookmark in self.workspace_bookmarks:
            name = bookmark.get("name", "").strip()
            path = bookmark.get("path", "").strip()
            if not name:
                report.add_error("A workspace bookmark is missing its name.")
                continue
            if not path:
                report.add_error(f"Workspace bookmark '{name}' is missing its path.")
                continue
            if name.lower() in seen_bookmarks:
                report.add_error(f"Duplicate workspace bookmark name: {name}")
            seen_bookmarks.add(name.lower())

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

    def _validate_profiles(self, report: ValidationReport) -> None:
        profiles = self.llm.get("profiles", [])
        if not profiles:
            report.add_error("llm.profiles is empty; at least one profile is required.")
            return

        seen_ids: set = set()
        for profile in profiles:
            pid = str(profile.get("id", "")).strip()
            if not pid:
                report.add_error("A profile is missing its id field.")
                continue
            if pid in seen_ids:
                report.add_error(f"Duplicate profile id: {pid}")
            seen_ids.add(pid)

            base_url = str(profile.get("base_url", "")).strip()
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                report.add_error(f"Profile '{pid}' base_url is not a valid http/https URL: {base_url!r}")

            model = str(profile.get("model", "")).strip()
            if not model:
                report.add_error(f"Profile '{pid}' is missing its model field.")

            try:
                context_window = int(profile.get("context_window", self.llm["defaults"]["context_window"]))
            except (TypeError, ValueError):
                context_window = -1
            if context_window <= 0:
                report.add_error(f"Profile '{pid}' context_window must be > 0.")

            try:
                max_tokens = int(profile.get("max_tokens", self.llm["defaults"]["max_tokens"]))
            except (TypeError, ValueError):
                max_tokens = -1
            if max_tokens <= 0:
                report.add_error(f"Profile '{pid}' max_tokens must be > 0.")

            if context_window > 0 and max_tokens > context_window:
                report.add_error(
                    f"Profile '{pid}' max_tokens ({max_tokens}) cannot exceed context_window ({context_window})."
                )

            try:
                temperature = float(profile.get("temperature", self.llm["defaults"]["temperature"]))
            except (TypeError, ValueError):
                temperature = -1.0
            if temperature < 0 or temperature > 2:
                report.add_error(f"Profile '{pid}' temperature {temperature} must be between 0 and 2.")

            api_key = str(profile.get("api_key", ""))
            api_key_env = str(profile.get("api_key_env", "")).strip()
            if api_key and api_key_env:
                report.add_warning(
                    f"Profile '{pid}' has both api_key (inline) and api_key_env set; "
                    "inline key takes precedence at runtime."
                )

        active_profile = str(self.llm.get("active_profile", "")).strip()
        if active_profile and active_profile not in seen_ids:
            report.add_warning(f"active_profile '{active_profile}' not found in profiles list.")
            if profiles:
                self.llm["active_profile"] = profiles[0]["id"]
        elif not active_profile and profiles:
            self.llm["active_profile"] = profiles[0]["id"]

    def _validate_legacy_providers(self, report: ValidationReport) -> None:
        providers = self.llm.get("providers", [])
        if not providers:
            report.add_error("llm.providers is empty; at least one provider is required.")
            return

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

    # ---- commit -------------------------------------------------------------------

    def apply_to(
        self,
        config: Config,
        *,
        backup: bool = True,
        allow_inline_key: bool = True,
        allowed_inline_providers: Optional[Iterable[str]] = None,
    ) -> ValidationReport:
        """Commit the draft to *config* and persist.

        - Runs validation first; refused if there are errors.
        - When ``backup=True`` writes a timestamped backup before overwriting.
        - 0.2.5 defaults ``allow_inline_key=True`` because plaintext keys in
          config.json are the product default.
        - ``allowed_inline_providers`` narrows inline-key persistence to the
          providers the current UI flow explicitly authorized (legacy path).
        - On save failure, the existing config file is restored from the backup
          and the in-memory ``Config`` state is reloaded from disk.
        """
        report = self.validate()
        if not report.ok:
            return report

        # Legacy provider path: strip inline keys unless authorized.
        if not self.llm.get("profiles"):
            allowed_inline_names = (
                {str(name).strip() for name in allowed_inline_providers if str(name).strip()}
                if allowed_inline_providers is not None
                else None
            )
            if not allow_inline_key or allowed_inline_names is not None:
                for provider in self.llm["providers"]:
                    if allow_inline_key and allowed_inline_names is not None and provider.get("name") in allowed_inline_names:
                        continue
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
            "model_roles": copy.deepcopy(getattr(config, "model_roles", {})),
            "workspace_bookmarks": copy.deepcopy(getattr(config, "workspace_bookmarks", [])),
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
        config.model_roles = copy.deepcopy(self.model_roles)
        config.workspace_bookmarks = copy.deepcopy(self.workspace_bookmarks)
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
            config.model_roles = previous_state["model_roles"]
            config.workspace_bookmarks = previous_state["workspace_bookmarks"]
            config.load()
            config._sync_runtime_fields()
            report.add_error(f"Failed to save config: {exc}")
            return report

        return report

    # ---- import / export ---------------------------------------------------------

    def export_config(self, *, with_keys: bool = False) -> Dict[str, Any]:
        """Return a serializable copy of the draft, redacting keys by default."""
        data = {
            "llm": copy.deepcopy(self.llm),
            "context_management": copy.deepcopy(self.context_management_defaults),
            "ui": copy.deepcopy(self.ui),
            "sessions": copy.deepcopy(self.sessions),
            "workspace_root": self.workspace_root,
            "skills_dir": self.skills_dir,
            "shell_type": self.shell_type,
            "authorization_level": self.authorization_level,
            "plan_mode": self.plan_mode,
            "thinking_mode": self.thinking_mode,
            "policy": copy.deepcopy(self.policy),
            "model_roles": copy.deepcopy(self.model_roles),
            "workspace_bookmarks": copy.deepcopy(self.workspace_bookmarks),
        }
        for key, value in self.extra_fields.items():
            if key not in data:
                data[key] = copy.deepcopy(value)

        if not with_keys:
            if data["llm"].get("profiles"):
                for profile in data["llm"]["profiles"]:
                    if profile.get("api_key"):
                        profile["api_key"] = mask_key(profile["api_key"])
            else:
                for provider in data["llm"].get("providers", []):
                    if provider.get("api_key"):
                        provider["api_key"] = mask_key(provider["api_key"])
        return data

    def import_config(self, path: str) -> ValidationReport:
        """Load a config file into the draft, replacing current state.

        The caller must call :meth:`apply_to` to persist.
        """
        report = ValidationReport()
        source_path = Path(path).expanduser()
        if not source_path.exists():
            report.add_error(f"Import file not found: {path}")
            return report
        try:
            with open(source_path, "r", encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except Exception as exc:
            report.add_error(f"Failed to parse import file: {exc}")
            return report

        if not isinstance(data, dict):
            report.add_error("Import file must contain a JSON object.")
            return report

        # Apply known fields into the draft.
        temp_config = Config(config_path=str(source_path))
        self._reset_from(temp_config)
        self.extra_fields = copy.deepcopy(temp_config._extra_fields)
        report = self.validate()
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
        if self.llm.get("profiles"):
            if not self.llm["profiles"]:
                self.llm["active_profile"] = ""
                return
            if not self.llm.get("active_profile") or not self._get_profile(self.llm["active_profile"]):
                self.llm["active_profile"] = self.llm["profiles"][0]["id"]
            return
        self._ensure_active_legacy()
