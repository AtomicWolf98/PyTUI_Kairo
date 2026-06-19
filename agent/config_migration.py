"""Legacy configuration migration helpers.

Handles the older flat ``model_profiles`` / ``models`` / ``model_options``
structures and converts them into the current ``llm.providers`` schema.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from agent.provider_registry import normalize_model


def _normalize_legacy_profile(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        name = value.strip()
        if not name:
            return None
        return {"name": name, "model": name}

    if not isinstance(value, dict):
        return None

    profile = dict(value)
    name = str(profile.get("name") or profile.get("model") or "").strip()
    if not name:
        return None
    profile["name"] = name
    return profile


def build_llm_from_legacy(
    data: Dict[str, Any],
    normalize_context_management: Callable[[Any], Dict[str, Any]],
    normalize_llm_defaults: Callable[[Any], Dict[str, Any]],
    fallback_base_url: str,
    fallback_model: str,
) -> Dict[str, Any]:
    """Convert legacy config dict into the provider-centric ``llm`` structure."""
    defaults = normalize_llm_defaults(data)
    fallback_api_key = str(data.get("api_key", ""))
    configured_profiles = data.get("model_profiles")
    if configured_profiles is None:
        configured_profiles = data.get("models", data.get("model_options", []))

    providers: List[Dict[str, Any]] = []
    provider_index: Dict[str, Dict[str, Any]] = {}
    if isinstance(configured_profiles, list) and configured_profiles:
        for item in configured_profiles:
            profile = _normalize_legacy_profile(item)
            if not profile:
                continue
            provider_name = profile["name"]
            provider = provider_index.get(provider_name)
            if provider is None:
                api_key = str(profile.get("api_key", fallback_api_key))
                api_key_env = str(profile.get("api_key_env", "")).strip()
                provider = {
                    "name": provider_name,
                    "base_url": str(profile.get("base_url", fallback_base_url)).strip(),
                    "api_key": api_key,
                    "api_key_env": api_key_env,
                    "_api_key_source": "file" if api_key else ("env" if api_key_env else "none"),
                    "models": [],
                }
                provider_index[provider_name] = provider
                providers.append(provider)
            model = {
                "name": str(profile.get("model", provider_name)).strip() or provider_name,
                "temperature": float(profile.get("temperature", defaults["temperature"])),
                "max_tokens": int(profile.get("max_tokens", defaults["max_tokens"])),
                "context_window": int(profile.get("context_window", defaults["context_window"])),
                "legacy_profile_name": provider_name,
            }
            if isinstance(profile.get("context_management"), dict):
                model["context_management"] = normalize_context_management(profile["context_management"])
            provider["models"].append(model)
    else:
        provider_name = "default"
        api_key = str(fallback_api_key)
        provider = {
            "name": provider_name,
            "base_url": fallback_base_url,
            "api_key": api_key,
            "api_key_env": "",
            "_api_key_source": "file" if api_key else "none",
            "models": [],
        }
        raw_models = data.get("models", data.get("model_options", []))
        if isinstance(raw_models, list) and raw_models:
            for item in raw_models:
                model = normalize_model(item, normalize_context_management)
                if not model:
                    continue
                model.setdefault("temperature", defaults["temperature"])
                model.setdefault("max_tokens", defaults["max_tokens"])
                model.setdefault("context_window", defaults["context_window"])
                provider["models"].append(model)
        if not provider["models"]:
            provider["models"].append({
                "name": str(data.get("model", fallback_model)).strip() or fallback_model,
                "temperature": defaults["temperature"],
                "max_tokens": defaults["max_tokens"],
                "context_window": defaults["context_window"],
            })
        providers.append(provider)

    active_legacy = str(data.get("active_model_profile", data.get("active_model", ""))).strip()
    active_provider = ""
    active_model = ""
    if active_legacy:
        for provider in providers:
            for model in provider["models"]:
                aliases = {provider["name"], model["name"], model.get("legacy_profile_name", "")}
                if active_legacy in aliases:
                    active_provider = provider["name"]
                    active_model = model["name"]
                    break
            if active_provider:
                break

    if not active_provider and providers:
        first_provider = providers[0]
        active_provider = first_provider["name"]
        active_model = first_provider["models"][0]["name"]

    return {
        "active_provider": active_provider,
        "active_model": active_model,
        "defaults": defaults,
        "providers": providers,
    }
