"""Provider and model normalization helpers used by the configuration layer."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


LLM_DEFAULTS = {
    "temperature": 0.2,
    "max_tokens": 4000,
    "context_window": 128000,
}


def normalize_model(value: Any, normalize_context_management: callable) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        name = value.strip()
        if not name:
            return None
        return {"name": name}

    if not isinstance(value, dict):
        return None

    model = dict(value)
    name = str(model.get("name") or model.get("model") or "").strip()
    if not name:
        return None
    model["name"] = name
    if "temperature" in model:
        model["temperature"] = float(model["temperature"])
    if "max_tokens" in model:
        model["max_tokens"] = int(model["max_tokens"])
    if "context_window" in model:
        model["context_window"] = int(model["context_window"])
    if "context_management" in model:
        model["context_management"] = normalize_context_management(model["context_management"])
    return model


def normalize_provider(
    value: Any,
    normalize_context_management: callable,
    api_key_source: str = "file",
) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    provider = dict(value)
    name = str(provider.get("name") or "").strip()
    if not name:
        return None

    models_value = provider.get("models", [])
    if not models_value and provider.get("model"):
        models_value = [{
            "name": provider.get("model"),
            "temperature": provider.get("temperature"),
            "max_tokens": provider.get("max_tokens"),
            "context_window": provider.get("context_window"),
        }]

    models: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in models_value if isinstance(models_value, list) else []:
        model = normalize_model(item, normalize_context_management)
        if not model:
            continue
        if model["name"] in seen:
            continue
        models.append(model)
        seen.add(model["name"])

    api_key = str(provider.get("api_key", ""))
    api_key_env = str(provider.get("api_key_env", "")).strip()
    return {
        "name": name,
        "base_url": str(provider.get("base_url", "")).strip(),
        "api_key": api_key,
        "api_key_env": api_key_env,
        "_api_key_source": "file" if api_key else ("env" if api_key_env else "none"),
        "models": models,
    }


def normalize_providers(
    value: Any,
    normalize_context_management: callable,
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    providers: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in value:
        provider = normalize_provider(item, normalize_context_management)
        if not provider:
            continue
        if provider["name"] in seen:
            continue
        providers.append(provider)
        seen.add(provider["name"])
    return providers


def resolve_profile_choice(
    choice: str,
    providers: List[Dict[str, Any]],
) -> Optional[Tuple[str, str]]:
    selected = choice.strip()
    if not selected:
        return None
    if " / " in selected:
        provider_name, model_name = selected.split(" / ", 1)
        provider = next((p for p in providers if p["name"] == provider_name), None)
        if provider and any(m["name"] == model_name for m in provider["models"]):
            return provider_name, model_name
    for provider in providers:
        if provider["name"] == selected and len(provider["models"]) == 1:
            return provider["name"], provider["models"][0]["name"]
        for model in provider["models"]:
            aliases = {model["name"], model.get("legacy_profile_name", "")}
            if selected in aliases:
                return provider["name"], model["name"]
    return None


def get_provider(providers: List[Dict[str, Any]], provider_name: str) -> Optional[Dict[str, Any]]:
    return next((provider for provider in providers if provider["name"] == provider_name), None)


def get_model(provider: Dict[str, Any], model_name: str) -> Optional[Dict[str, Any]]:
    return next((model for model in provider.get("models", []) if model["name"] == model_name), None)
