from __future__ import annotations

from tender_agent.llm.base import LLMProvider
from tender_agent.llm.openai_compatible import OpenAICompatibleProvider
from tender_agent.llm.stub import StubLLMProvider


def create_llm_provider(
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> LLMProvider:
    normalized = provider_name.strip().lower()
    if normalized in {"openai", "openai_compatible", "compatible"}:
        return OpenAICompatibleProvider(api_key=api_key, model=model, base_url=base_url)
    if normalized == "stub":
        return StubLLMProvider(model=model)
    raise RuntimeError(
        f"Unsupported LLM provider '{provider_name}'. Supported values: openai, openai_compatible, stub."
    )
