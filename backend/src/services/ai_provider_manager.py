"""
AI Provider Manager
====================
Single choke point for Mistral chat-completion calls with automatic failover
across multiple API keys. A single rate-limited/expired key was escalating
tickets ("System error: Rate limited") that a second key could have handled —
this exists to try the next configured key before giving up.

Do not construct a per-call OpenAI(api_key=...) client elsewhere for ticket
reply generation — go through get_provider_manager() so every caller gets the
same failover/backoff/logging behavior instead of duplicating it.
"""
import os
import time
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .mistral_limiter import call_with_limit

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
DEFAULT_BASE_URL = os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
MAX_BACKOFF_SECONDS = 4


@dataclass
class _Provider:
    label: str
    api_key: str
    model: str
    base_url: str = DEFAULT_BASE_URL


class AllProvidersFailedError(Exception):
    """Raised when every configured Mistral key has failed for this request."""

    def __init__(self, attempts: List[Dict[str, str]]):
        self.attempts = attempts
        reasons = "; ".join(f"{a['label']}={a['reason']}" for a in attempts) or "no keys configured"
        super().__init__(f"All AI providers failed: {reasons}")


def _describe(error: Exception) -> str:
    """Classify a failure for logging — never includes the API key."""
    status = getattr(error, "status_code", None)
    text = str(error).lower()
    if status == 429 or "429" in text or "rate limit" in text:
        return "rate_limited"
    if "quota" in text:
        return "quota_exceeded"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if status and status >= 500:
        return f"provider_error_{status}"
    return "temporary_failure"


class AIProviderManager:
    """Tries a primary Mistral key, then fallback keys in order, on failure."""

    def __init__(self):
        self._providers = self._load_providers()
        self._clients: Dict[str, OpenAI] = {}
        if not self._providers:
            logger.warning("[AI_PROVIDER] No Mistral API keys configured — AI replies will escalate immediately")
        else:
            logger.info(f"[AI_PROVIDER] Configured providers: {[p.label for p in self._providers]}")

    def _load_providers(self) -> List[_Provider]:
        providers = []
        primary_key = (
            os.getenv("MISTRAL_API_KEY_PRIMARY")
            or os.getenv("MISTRAL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if primary_key:
            providers.append(_Provider("primary", primary_key, os.getenv("MISTRAL_MODEL_PRIMARY", DEFAULT_MODEL)))
        for i in (1, 2, 3):
            key = os.getenv(f"MISTRAL_API_KEY_FALLBACK_{i}")
            if key:
                providers.append(_Provider(f"fallback_{i}", key, os.getenv(f"MISTRAL_MODEL_FALLBACK_{i}", DEFAULT_MODEL)))

        # Last-resort fallback on a different provider entirely (Groq), tried only
        # after every Mistral key above has failed — keeps ticket replies flowing
        # if Mistral's account-wide rate limit or quota is hit, not just one key.
        # Mirrors the Mistral primary+fallback_N scheme: GROQ_API_KEY is the first
        # Groq key, GROQ_API_KEY_FALLBACK_{i} are additional ones tried after it.
        groq_default_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        groq_default_base_url = os.getenv("GROQ_API_BASE_URL", "https://api.groq.com/openai/v1")
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            providers.append(_Provider("groq_fallback_1", groq_key, groq_default_model, base_url=groq_default_base_url))
        for i in (1, 2, 3):
            key = os.getenv(f"GROQ_API_KEY_FALLBACK_{i}")
            if key:
                providers.append(_Provider(
                    f"groq_fallback_{i + 1}",
                    key,
                    os.getenv(f"GROQ_MODEL_FALLBACK_{i}", groq_default_model),
                    base_url=os.getenv(f"GROQ_API_BASE_URL_FALLBACK_{i}", groq_default_base_url),
                ))
        return providers

    def _client_for(self, provider: _Provider) -> OpenAI:
        if provider.label not in self._clients:
            self._clients[provider.label] = OpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                max_retries=1,
                timeout=15.0,
            )
        return self._clients[provider.label]

    @property
    def has_providers(self) -> bool:
        return bool(self._providers)

    async def create_chat_completion(
        self,
        *,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        response_format: Optional[dict] = None,
    ):
        """
        Tries each configured provider in order (same messages/temperature/RAG
        context for every attempt — failover changes the key, not the prompt).
        Returns (response, provider_label, model, usage). Raises
        AllProvidersFailedError if every provider fails. Never retries more
        than len(providers) times.

        usage is a dict: {prompt_tokens, completion_tokens, total_tokens}
        (each None if the provider's response didn't include a `.usage`
        block — never fabricated as 0, which would be indistinguishable from
        a genuinely free/zero-token call), plus latency_ms (wall-clock time
        for this whole call, including any failed attempts before the
        successful one) and attempts (1-indexed count of providers tried,
        so a first-try success is 1, not 0).
        """
        if not self._providers:
            raise AllProvidersFailedError([{"label": "none", "reason": "no API keys configured"}])

        attempts: List[Dict[str, str]] = []
        call_start = time.monotonic()

        for i, provider in enumerate(self._providers):
            client = self._client_for(provider)
            kwargs = {"model": provider.model, "messages": messages, "temperature": temperature}
            if response_format is not None:
                kwargs["response_format"] = response_format

            kind = "fallback attempt" if i > 0 else "attempt"
            logger.info(f"[AI_PROVIDER] {kind} start provider={provider.label} model={provider.model}")
            t_start = time.monotonic()
            try:
                response = await call_with_limit(lambda kw=kwargs, c=client: c.chat.completions.create(**kw))
            except TypeError:
                # Some models/providers don't support response_format — retry once
                # without it on the same key before counting this provider as failed.
                logger.warning(f"[AI_PROVIDER] {provider.label} rejected response_format param, retrying without it")
                try:
                    kwargs.pop("response_format", None)
                    response = await call_with_limit(lambda kw=kwargs, c=client: c.chat.completions.create(**kw))
                except Exception as e2:
                    attempts.append({"label": provider.label, "reason": _describe(e2)})
                    logger.warning(f"[AI_PROVIDER] {provider.label} failed reason={_describe(e2)}")
                    response = None
            except Exception as e:
                attempts.append({"label": provider.label, "reason": _describe(e)})
                logger.warning(f"[AI_PROVIDER] {provider.label} failed reason={_describe(e)} after {time.monotonic() - t_start:.2f}s")
                response = None

            if response is not None:
                elapsed = time.monotonic() - t_start
                total = time.monotonic() - call_start
                logger.info(
                    f"[AI_PROVIDER] success provider={provider.label} model={provider.model} "
                    f"response_time={elapsed:.2f}s total_time={total:.2f}s"
                )
                raw_usage = getattr(response, "usage", None)
                usage = {
                    "prompt_tokens": getattr(raw_usage, "prompt_tokens", None) if raw_usage else None,
                    "completion_tokens": getattr(raw_usage, "completion_tokens", None) if raw_usage else None,
                    "total_tokens": getattr(raw_usage, "total_tokens", None) if raw_usage else None,
                    "latency_ms": round(total * 1000),
                    "attempts": i + 1,
                    "provider": provider.label,
                    "model": provider.model,
                }
                return response, provider.label, provider.model, usage

            is_last = i == len(self._providers) - 1
            if not is_last:
                backoff = min(2 ** i, MAX_BACKOFF_SECONDS)
                logger.info(f"[AI_PROVIDER] switching to next provider in {backoff}s")
                await asyncio.sleep(backoff)

        logger.error(
            f"[AI_PROVIDER] all {len(self._providers)} provider(s) exhausted "
            f"total_time={time.monotonic() - call_start:.2f}s"
        )
        raise AllProvidersFailedError(attempts)


# Global instance — mirrors the module-level singleton pattern used elsewhere
# (message_processor, actions_service, etc.)
ai_provider_manager = AIProviderManager()
