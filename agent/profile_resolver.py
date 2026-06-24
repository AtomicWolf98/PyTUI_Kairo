"""Profile resolution layer for Kairo 0.2.5.

Unifies the new ``llm.profiles[]`` structure with the legacy ``llm.providers[]``
schema and exposes a single :class:`ResolvedProfile` dataclass for runtime use.
"""
from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.provider_registry import (
    LLM_DEFAULTS,
    get_provider,
    resolve_profile_choice,
)


@dataclass
class ResolvedProfile:
    """Runtime view of a configured model profile."""

    id: str
    label: str
    provider: str
    base_url: str
    model: str
    api_key: str
    api_key_source: str
    temperature: float
    max_tokens: int
    context_window: int
    context_management: Dict[str, Any]


def mask_key(key: str) -> str:
    """Return a safe preview of an API key.

    - Empty key -> "missing"
    - Short key (<=8) -> "********"
    - Otherwise -> "sk...abcd" style (first 2 + ... + last 4)
    """
    if not key:
        return "missing"
    if len(key) <= 8:
        return "********"
    return key[:2] + "..." + key[-4:]


def describe_key_source(key: str, source: str) -> str:
    """Human description of where a profile's API key came from."""
    source = source or "none"
    if source == "env":
        return "env"
    if source == "override":
        return "runtime override"
    if source == "file":
        return f"inline ({mask_key(key)})"
    return "missing"


def _normalize_context_management(value: Any) -> Dict[str, Any]:
    from agent.config import CONTEXT_MANAGEMENT_DEFAULTS

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


def _normalize_profile(value: Any, defaults: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    profile = dict(value)
    pid = str(profile.get("id") or profile.get("name") or "").strip()
    if not pid:
        return None
    profile["id"] = pid
    if "label" not in profile:
        profile["label"] = ""
    if "provider" not in profile:
        profile["provider"] = ""
    base_url = str(profile.get("base_url", "")).strip()
    if not base_url:
        base_url = str(defaults.get("base_url", "")).strip()
    profile["base_url"] = base_url
    profile["api_key"] = str(profile.get("api_key", ""))
    profile["api_key_env"] = str(profile.get("api_key_env", "")).strip()
    profile["model"] = str(profile.get("model") or profile.get("name") or pid).strip()
    profile["temperature"] = float(profile.get("temperature", defaults.get("temperature", LLM_DEFAULTS["temperature"])))
    profile["max_tokens"] = int(profile.get("max_tokens", defaults.get("max_tokens", LLM_DEFAULTS["max_tokens"])))
    profile["context_window"] = int(profile.get("context_window", defaults.get("context_window", LLM_DEFAULTS["context_window"])))
    if isinstance(profile.get("context_management"), dict):
        profile["context_management"] = _normalize_context_management(profile["context_management"])
    else:
        profile["context_management"] = _normalize_context_management(defaults.get("context_management"))
    profile.setdefault("_api_key_source", "file" if profile["api_key"] else ("env" if profile["api_key_env"] else "none"))
    return profile


def _build_profiles_from_providers(llm: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert legacy providers[] into profile-shaped dicts."""
    profiles: List[Dict[str, Any]] = []
    defaults = llm.get("defaults", dict(LLM_DEFAULTS))
    for provider in llm.get("providers", []):
        provider_name = str(provider.get("name", "")).strip()
        if not provider_name:
            continue
        for model in provider.get("models", []):
            model_name = str(model.get("name", "")).strip()
            if not model_name:
                continue
            pid = f"{provider_name}/{model_name}"
            profile = {
                "id": pid,
                "label": "",
                "provider": provider_name,
                "base_url": str(provider.get("base_url", "")).strip(),
                "api_key": str(provider.get("api_key", "")),
                "api_key_env": str(provider.get("api_key_env", "")).strip(),
                "model": model_name,
                "temperature": float(model.get("temperature", defaults.get("temperature", LLM_DEFAULTS["temperature"]))),
                "max_tokens": int(model.get("max_tokens", defaults.get("max_tokens", LLM_DEFAULTS["max_tokens"]))),
                "context_window": int(model.get("context_window", defaults.get("context_window", LLM_DEFAULTS["context_window"]))),
                "context_management": _normalize_context_management(model.get("context_management")),
                "_api_key_source": provider.get("_api_key_source", "file" if provider.get("api_key") else ("env" if provider.get("api_key_env") else "none")),
                "_legacy_provider": provider_name,
            }
            profiles.append(profile)
    return profiles


def _resolve_key(profile: Dict[str, Any], legacy_provider: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Resolve API key using 0.2.5 priority rules.

    Priority:
      1. profile.api_key
      2. profile.api_key_env environment variable
      3. legacy provider.api_key
      4. legacy provider.api_key_env environment variable
      5. empty
    """
    inline = str(profile.get("api_key", "")).strip()
    if inline:
        return inline, "file"

    env_name = str(profile.get("api_key_env", "")).strip()
    if env_name and os.environ.get(env_name):
        return os.environ[env_name], "env"

    if legacy_provider:
        legacy_inline = str(legacy_provider.get("api_key", "")).strip()
        if legacy_inline:
            return legacy_inline, "file"
        legacy_env = str(legacy_provider.get("api_key_env", "")).strip()
        if legacy_env and os.environ.get(legacy_env):
            return os.environ[legacy_env], "env"

    return "", "none"


def resolve_profile(
    config,
    profile_id: Optional[str] = None,
    role: str = "chat",
) -> Optional[ResolvedProfile]:
    """Resolve a profile for runtime use.

    Resolution order:
      1. Explicit ``profile_id`` if provided.
      2. ``model_roles[role]`` if configured.
      3. ``llm.active_profile`` if configured.
      4. Legacy ``llm.active_provider`` / ``llm.active_model``.
    """
    llm = config.llm if hasattr(config, "llm") else {}
    model_roles = getattr(config, "model_roles", None) or {}

    chosen_id = profile_id
    if chosen_id is None:
        chosen_id = model_roles.get(role)
    if chosen_id is None:
        chosen_id = llm.get("active_profile")
    if chosen_id is None:
        provider_name = llm.get("active_provider", "")
        model_name = llm.get("active_model", "")
        if provider_name and model_name:
            chosen_id = f"{provider_name}/{model_name}"

    profiles = _list_profiles(config)
    profile = None
    legacy_provider = None
    if chosen_id:
        # Try exact id match first.
        for p in profiles:
            if p["id"] == chosen_id:
                profile = p
                break
        # Try legacy provider/model or alias resolution.
        if profile is None:
            choice = resolve_profile_choice(chosen_id, llm.get("providers", []))
            if choice:
                provider_name, model_name = choice
                for p in profiles:
                    if p.get("provider") == provider_name and p["model"] == model_name:
                        profile = p
                        break

    if profile is None and profiles:
        profile = profiles[0]

    if profile is None:
        return None

    # Resolve legacy provider for key fallback.
    provider_name = profile.get("provider") or profile.get("_legacy_provider", "")
    if provider_name:
        legacy_provider = get_provider(llm.get("providers", []), provider_name)

    api_key, api_key_source = _resolve_key(profile, legacy_provider)

    return ResolvedProfile(
        id=profile["id"],
        label=str(profile.get("label", "")).strip() or profile["id"],
        provider=str(profile.get("provider", "")).strip(),
        base_url=str(profile.get("base_url", "")).strip(),
        model=profile["model"],
        api_key=api_key,
        api_key_source=api_key_source,
        temperature=float(profile.get("temperature", LLM_DEFAULTS["temperature"])),
        max_tokens=int(profile.get("max_tokens", LLM_DEFAULTS["max_tokens"])),
        context_window=int(profile.get("context_window", LLM_DEFAULTS["context_window"])),
        context_management=deepcopy(profile.get("context_management", _normalize_context_management(None))),
    )


def get_active_profile(config) -> Optional[ResolvedProfile]:
    """Return the currently active profile."""
    return resolve_profile(config, profile_id=None, role="chat")


def list_profiles(config) -> List[ResolvedProfile]:
    """Return all configured profiles as resolved runtime views."""
    profiles = _list_profiles(config)
    resolved: List[ResolvedProfile] = []
    for profile in profiles:
        provider_name = profile.get("provider") or profile.get("_legacy_provider", "")
        legacy_provider = get_provider(config.llm.get("providers", []), provider_name) if provider_name else None
        api_key, api_key_source = _resolve_key(profile, legacy_provider)
        resolved.append(
            ResolvedProfile(
                id=profile["id"],
                label=str(profile.get("label", "")).strip() or profile["id"],
                provider=str(profile.get("provider", "")).strip(),
                base_url=str(profile.get("base_url", "")).strip(),
                model=profile["model"],
                api_key=api_key,
                api_key_source=api_key_source,
                temperature=float(profile.get("temperature", LLM_DEFAULTS["temperature"])),
                max_tokens=int(profile.get("max_tokens", LLM_DEFAULTS["max_tokens"])),
                context_window=int(profile.get("context_window", LLM_DEFAULTS["context_window"])),
                context_management=deepcopy(profile.get("context_management", _normalize_context_management(None))),
            )
        )
    return resolved


def _list_profiles(config) -> List[Dict[str, Any]]:
    """Return raw profile dicts from config (new structure first, legacy fallback)."""
    llm = config.llm if hasattr(config, "llm") else {}
    raw_profiles = llm.get("profiles")
    if isinstance(raw_profiles, list) and raw_profiles:
        defaults = llm.get("defaults", dict(LLM_DEFAULTS))
        normalized: List[Dict[str, Any]] = []
        seen: set = set()
        for item in raw_profiles:
            profile = _normalize_profile(item, defaults)
            if not profile:
                continue
            if profile["id"] in seen:
                continue
            seen.add(profile["id"])
            normalized.append(profile)
        return normalized
    return _build_profiles_from_providers(llm)
