"""Built-in provider templates for the first-run wizard and quick-add menus.

Templates are intentionally Python constants (per the planning doc
"prefer Python constants") so they remain usable offline and are easy to test.
The data is a snapshot of commonly used OpenAI-compatible endpoints; users are
warned in the docs that templates are a starting point and may go stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ModelTemplate:
    name: str
    context_window: int = 128_000
    max_tokens: int = 4096
    temperature: float = 0.2


@dataclass
class ProviderTemplate:
    name: str
    base_url: str
    api_key_env: str
    description: str = ""
    models: List[ModelTemplate] = field(default_factory=list)

    def as_default_provider_dict(self) -> Dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "models": [
                {
                    "name": model.name,
                    "temperature": model.temperature,
                    "max_tokens": model.max_tokens,
                    "context_window": model.context_window,
                }
                for model in self.models
            ],
        }


_TEMPLATES: Dict[str, ProviderTemplate] = {
    "OpenAI": ProviderTemplate(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        description="Official OpenAI API. OpenAI-compatible.",
        models=[
            ModelTemplate("gpt-4o", context_window=128_000, max_tokens=16_384),
            ModelTemplate("gpt-4o-mini", context_window=128_000, max_tokens=16_384),
        ],
    ),
    "DeepSeek": ProviderTemplate(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="KAIRO_DEEPSEEK_API_KEY",
        description="DeepSeek chat & code. OpenAI-compatible.",
        models=[ModelTemplate("deepseek-chat", context_window=64_000, max_tokens=8_000)],
    ),
    "MiniMax": ProviderTemplate(
        name="minimax",
        base_url="https://api.minimaxi.com/v1",
        api_key_env="KAIRO_MINIMAX_API_KEY",
        description="MiniMax M-series models.",
        models=[ModelTemplate("MiniMax-M3", context_window=128_000, max_tokens=40_000)],
    ),
    "Moonshot / Kimi": ProviderTemplate(
        name="moonshot",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="KAIRO_MOONSHOT_API_KEY",
        description="Kimi series models.",
        models=[ModelTemplate("moonshot-v1-32k", context_window=32_000, max_tokens=8_000)],
    ),
    "Qwen compatible": ProviderTemplate(
        name="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="KAIRO_QWEN_API_KEY",
        description="Alibaba DashScope OpenAI-compatible endpoint.",
        models=[ModelTemplate("qwen-plus", context_window=128_000, max_tokens=8_000)],
    ),
    "OpenRouter": ProviderTemplate(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        description="Routing router over many providers; OpenAI-compatible.",
        models=[ModelTemplate("openai/gpt-4o-mini", context_window=128_000, max_tokens=4_000)],
    ),
    "Local OpenAI-compatible": ProviderTemplate(
        name="local",
        base_url="http://127.0.0.1:8000/v1",
        api_key_env="KAIRO_LOCAL_API_KEY",
        description="LM Studio, vLLM, Ollama with OpenAI shim, etc.",
        models=[ModelTemplate("local-model", context_window=32_000, max_tokens=4_000)],
    ),
    "Custom": ProviderTemplate(
        name="custom",
        base_url="",
        api_key_env="",
        description="Provide every field yourself.",
        models=[],
    ),
}


def list_templates() -> List[str]:
    return list(_TEMPLATES.keys())


def get_template(name: str) -> Optional[ProviderTemplate]:
    return _TEMPLATES.get(name)


def all_templates() -> Dict[str, ProviderTemplate]:
    return dict(_TEMPLATES)