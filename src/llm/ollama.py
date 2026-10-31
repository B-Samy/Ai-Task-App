"""Ollama (local) provider. Needs no API key."""
from __future__ import annotations

import requests

from src.llm.base import (LLMBadResponse, LLMModelMissing, LLMProvider, LLMUnavailable, ProviderStatus)


class OllamaProvider(LLMProvider):
    name = "ollama"
    is_external = False

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2", session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = session or requests.Session()

    def installed_models(self, timeout: int = 5) -> list[str]:
        try:
            r = self.http.get(f"{self.base_url}/api/tags", timeout=timeout)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError) as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.base_url}. Install/start Ollama (https://ollama.com) and check the URL. "
                f"({type(exc).__name__})") from exc

    def check(self) -> ProviderStatus:
        try:
            names = self.installed_models()
        except LLMUnavailable as exc:
            return ProviderStatus(False, self.name, self.model, str(exc))
        base = self.model.split(":")[0]
        if not any(n == self.model or n.split(":")[0] == base and ":" not in self.model for n in names):
            return ProviderStatus(False, self.name, self.model,
                                  f"Ollama is running but model '{self.model}' is not installed. Run: ollama pull {self.model}"
                                  + (f" (installed: {', '.join(names)})" if names else ""))
        return ProviderStatus(True, self.name, self.model, "Connected.")

    def _chat_once(self, messages, json_mode, timeout):
        payload = {"model": self.model, "messages": messages, "stream": False, "options": {"temperature": 0.2}}
        if json_mode:
            payload["format"] = "json"
        try:
            r = self.http.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
        except requests.Timeout as exc:
            raise LLMUnavailable("Ollama timed out. The model may still be loading; try again or use a smaller model.") from exc
        except requests.RequestException as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.base_url} ({type(exc).__name__}).") from exc
        if r.status_code == 404:
            raise LLMModelMissing(f"Model '{self.model}' not found. Run: ollama pull {self.model}")
        if r.status_code >= 500:
            raise LLMUnavailable(f"Ollama server error ({r.status_code}).")
        if r.status_code >= 400:
            raise LLMBadResponse(f"Ollama rejected the request ({r.status_code}).")
        try:
            return r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMBadResponse("Unexpected response format from Ollama.") from exc
