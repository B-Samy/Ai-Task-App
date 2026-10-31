from __future__ import annotations

from src.config.settings import AppSettings
from src.llm.base import LLMProvider
from src.llm.ollama import OllamaProvider
from src.llm.openai_compat import OpenAICompatProvider


def get_provider(s: AppSettings) -> LLMProvider | None:
    if s.llm_provider == "ollama":
        return OllamaProvider(s.ollama_url, s.ollama_model)
    if s.llm_provider == "openai_compatible":
        return OpenAICompatProvider(s.hosted_base_url, s.hosted_model)
    return None
