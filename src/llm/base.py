"""Provider-neutral LLM interface and errors."""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Base class. Messages are safe to show to users (never contain secrets)."""


class LLMUnavailable(LLMError):
    """Server not reachable / not configured."""


class LLMModelMissing(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMRateLimited(LLMError):
    pass


class LLMBadResponse(LLMError):
    pass


@dataclass
class ProviderStatus:
    ok: bool
    provider: str
    model: str
    message: str


_SECRET_RE = re.compile(r"(?i)(bearer\s+|api[_-]?key[=:]\s*|sk-|gsk_)[A-Za-z0-9_\-\.]{6,}")


def redact(text: str, secret: str | None = None) -> str:
    """Remove anything that looks like a credential from text destined for logs or UI."""
    if secret:
        text = text.replace(secret, "***")
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", text)


class LLMProvider(ABC):
    name: str = "base"
    is_external: bool = False
    model: str = ""

    @abstractmethod
    def check(self) -> ProviderStatus: ...

    @abstractmethod
    def _chat_once(self, messages: list[dict[str, str]], json_mode: bool, timeout: int) -> str: ...

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False, timeout: int = 60,
             max_retries: int = 2, sleep: Callable[[float], None] = time.sleep) -> str:
        """Call the provider with bounded retries. Auth/model errors are never retried."""
        last: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return self._chat_once(messages, json_mode, timeout)
            except (LLMAuthError, LLMModelMissing) as exc:
                raise exc
            except (LLMUnavailable, LLMRateLimited, LLMBadResponse) as exc:
                last = exc
                log.warning("LLM call failed (attempt %d/%d): %s", attempt + 1, max_retries + 1, redact(str(exc)))
                if attempt < max_retries:
                    sleep(min(2 ** attempt, 8))
        assert last is not None
        raise last
