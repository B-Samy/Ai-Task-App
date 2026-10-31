"""OpenAI-compatible hosted provider (e.g. Groq's free tier). The key is read from the environment only."""
from __future__ import annotations

import os

import requests

from src.llm.base import (LLMAuthError, LLMBadResponse, LLMModelMissing, LLMProvider, LLMRateLimited,
                          LLMUnavailable, ProviderStatus)


class OpenAICompatProvider(LLMProvider):
    name = "openai_compatible"
    is_external = True
    ENV_KEYS = ("LLM_API_KEY", "GROQ_API_KEY")

    def __init__(self, base_url: str, model: str, api_key: str | None = None, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._key = api_key or next((os.environ[k] for k in self.ENV_KEYS if os.environ.get(k)), "")
        self.http = session or requests.Session()

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def check(self) -> ProviderStatus:
        if not self._key:
            return ProviderStatus(False, self.name, self.model,
                                  "No API key found. Set LLM_API_KEY (or GROQ_API_KEY) in your .env file and restart.")
        try:
            r = self.http.get(f"{self.base_url}/models", headers=self._headers(), timeout=8)
        except requests.RequestException as exc:
            return ProviderStatus(False, self.name, self.model, f"Cannot reach {self.base_url} ({type(exc).__name__}).")
        if r.status_code in (401, 403):
            return ProviderStatus(False, self.name, self.model, "The API key was rejected (401/403). Check the key.")
        if r.status_code == 429:
            return ProviderStatus(False, self.name, self.model, "Rate limited right now (429). Free tiers have quotas.")
        if r.status_code >= 400:
            return ProviderStatus(False, self.name, self.model, f"Provider returned HTTP {r.status_code}.")
        try:
            ids = [m.get("id") for m in r.json().get("data", [])]
        except ValueError:
            ids = []
        if ids and self.model not in ids:
            return ProviderStatus(False, self.name, self.model, f"Model '{self.model}' is not listed by the provider.")
        return ProviderStatus(True, self.name, self.model, "Connected.")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _chat_once(self, messages, json_mode, timeout):
        if not self._key:
            raise LLMAuthError("No API key configured. Add LLM_API_KEY to your .env file.")
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = self.http.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers(), timeout=timeout)
        except requests.Timeout as exc:
            raise LLMUnavailable("The hosted provider timed out.") from exc
        except requests.RequestException as exc:
            raise LLMUnavailable(f"Cannot reach the hosted provider ({type(exc).__name__}).") from exc
        if r.status_code in (401, 403):
            raise LLMAuthError("The API key was rejected by the provider.")
        if r.status_code == 404:
            raise LLMModelMissing(f"Model '{self.model}' was not found at the provider.")
        if r.status_code == 429:
            raise LLMRateLimited("Provider rate limit or free-tier quota reached. Wait and retry, or use Ollama.")
        if r.status_code >= 500:
            raise LLMUnavailable(f"Provider server error ({r.status_code}).")
        if r.status_code >= 400:
            raise LLMBadResponse(f"Provider rejected the request ({r.status_code}).")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("Unexpected response format from the provider.") from exc
