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


def merge_model_defaults(model: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing temperature/max_tokens/context_window from defaults (does not mutate)."""
    merged = dict(model)
    if "temperature" not in merged:
        merged["temperature"] = float(defaults.get("temperature", 0.2))
    if "max_tokens" not in merged:
        merged["max_tokens"] = int(defaults.get("max_tokens", 4000))
    if "context_window" not in merged:
        merged["context_window"] = int(defaults.get("context_window", 128000))
    merged["temperature"] = float(merged["temperature"])
    merged["max_tokens"] = int(merged["max_tokens"])
    merged["context_window"] = int(merged["context_window"])
    return merged


def redact_api_key(value: str) -> str:
    """Return a safe preview of an API key.

    - Empty/short keys return a fixed label.
    - Otherwise keep first 2 and last 4 characters (e.g. ``sk-...abcd``).
    """
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:2] + "..." + value[-4:]


def make_provider(
    *,
    name: str,
    base_url: str,
    models: List[Dict[str, Any]],
    api_key: str = "",
    api_key_env: str = "",
    normalize_context_management: Optional[callable] = None,
) -> Optional[Dict[str, Any]]:
    """Build a normalized provider dict from explicit field values."""
    raw = {
        "name": name,
        "base_url": base_url,
        "models": models,
    }
    if api_key:
        raw["api_key"] = api_key
    if api_key_env:
        raw["api_key_env"] = api_key_env
    if normalize_context_management is None:
        from agent.config import CONTEXT_MANAGEMENT_DEFAULTS  # local import to avoid cycle

        def normalize_context_management(value):
            settings = dict(CONTEXT_MANAGEMENT_DEFAULTS)
            if isinstance(value, dict):
                settings.update({key: value[key] for key in settings if key in value})
            settings["enabled"] = bool(settings["enabled"])
            settings["auto_compress"] = bool(settings["auto_compress"])
            settings["trigger_percent"] = min(100.0, max(1.0, float(settings["trigger_percent"])))
            settings["target_percent"] = min(
                settings["trigger_percent"], max(1.0, float(settings["target_percent"]))
            )
            settings["preserve_recent_turns"] = max(0, int(settings["preserve_recent_turns"]))
            return settings
    return normalize_provider(raw, normalize_context_management)


def make_model(
    *,
    name: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    context_window: Optional[int] = None,
    context_management: Optional[Dict[str, Any]] = None,
    normalize_context_management: Optional[callable] = None,
) -> Dict[str, Any]:
    """Build a normalized model dict from explicit field values."""
    raw: Dict[str, Any] = {"name": name}
    if temperature is not None:
        raw["temperature"] = temperature
    if max_tokens is not None:
        raw["max_tokens"] = max_tokens
    if context_window is not None:
        raw["context_window"] = context_window
    if context_management is not None:
        raw["context_management"] = context_management
    if normalize_context_management is None:
        from agent.config import CONTEXT_MANAGEMENT_DEFAULTS

        def normalize_context_management(value):
            settings = dict(CONTEXT_MANAGEMENT_DEFAULTS)
            if isinstance(value, dict):
                settings.update({key: value[key] for key in settings if key in value})
            settings["enabled"] = bool(settings["enabled"])
            settings["auto_compress"] = bool(settings["auto_compress"])
            settings["trigger_percent"] = min(100.0, max(1.0, float(settings["trigger_percent"])))
            settings["target_percent"] = min(
                settings["trigger_percent"], max(1.0, float(settings["target_percent"]))
            )
            settings["preserve_recent_turns"] = max(0, int(settings["preserve_recent_turns"]))
            return settings
    normalized = normalize_model(raw, normalize_context_management)
    return normalized or {"name": name}
