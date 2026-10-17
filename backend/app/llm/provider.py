from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai import APITimeoutError, OpenAI
from openai.types.chat import ChatCompletion

from .http_client import create_http_client
from .profiles import LlmProfile, validate_base_url

if TYPE_CHECKING:
    from httpx import BaseTransport


@dataclass(frozen=True)
class LlmCompletionResult:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_hit_rate: float | None = None


class LlmProvider:
    def __init__(self, transport: BaseTransport | None = None):
        self._transport = transport

    def chat_completion(
        self,
        profile: LlmProfile,
        messages: list[dict[str, str]],
    ) -> LlmCompletionResult:
        base_url = _client_base_url(profile.base_url)
        http_client = create_http_client(self._transport)
        with OpenAI(
            base_url=base_url,
            api_key=profile.api_key,
            timeout=profile.timeout,
            max_retries=0,
            http_client=http_client,
        ) as client:
            for attempt in range(profile.transport_retries + 1):
                try:
                    response = client.chat.completions.create(
                        model=profile.model,
                        messages=messages,
                        reasoning_effort=profile.reasoning_effort,
                    )
                    return _completion_result(response)
                except (APITimeoutError, TimeoutError, EmptyModelOutputError):
                    if attempt == profile.transport_retries:
                        raise
        raise RuntimeError("unreachable transport retry state")


class EmptyModelOutputError(ValueError):
    pass


def _completion_result(response: ChatCompletion) -> LlmCompletionResult:
    if not response.choices:
        raise ValueError("llm response did not include choices")
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("llm response did not include message content")
    if not content.strip():
        raise EmptyModelOutputError("llm response did not include non-empty message content")
    usage = response.usage
    prompt_tokens = _int_or_none(usage.prompt_tokens) if usage else None
    completion_tokens = _int_or_none(usage.completion_tokens) if usage else None
    total_tokens = _int_or_none(usage.total_tokens) if usage else None
    details = usage.prompt_tokens_details if usage else None
    cached_tokens = _int_or_none(details.cached_tokens) if details else None
    cache_hit_rate = (
        cached_tokens / prompt_tokens
        if cached_tokens is not None and prompt_tokens not in (None, 0)
        else None
    )
    return LlmCompletionResult(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        cache_hit_rate=cache_hit_rate,
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _client_base_url(base_url: str) -> str:
    validate_base_url(base_url)
    trimmed = base_url.rstrip("/")
    # Existing profiles may store the complete endpoint instead of the API prefix.
    suffix = "/chat/completions"
    return trimmed[:-len(suffix)] if trimmed.endswith(suffix) else trimmed
