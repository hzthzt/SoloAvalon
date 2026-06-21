from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .profiles import LlmProfile, validate_base_url


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class LlmCompletionResult:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_hit_rate: float | None = None


class LlmProvider:
    def __init__(self, transport: Transport | None = None):
        self._transport = transport or _urllib_transport

    def chat_completion(
        self,
        profile: LlmProfile,
        messages: list[dict[str, str]],
    ) -> LlmCompletionResult:
        payload = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
        }
        headers = {
            "Authorization": f"Bearer {profile.api_key}",
            "Content-Type": "application/json",
        }
        return self._send_with_retries(
            profile,
            _completion_url(profile.base_url),
            headers,
            payload,
        )

    def _send_with_retries(
        self,
        profile: LlmProfile,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> str:
        for retry_index in range(profile.timeout_retries + 1):
            try:
                response = self._transport(url, headers, payload, profile.timeout)
                return _completion_result(response)
            except urllib.error.HTTPError as exc:
                raise ConnectionError(_http_error_message(url, exc)) from exc
            except urllib.error.URLError as exc:
                raise ConnectionError(_connection_error_message(url, exc)) from exc
            except (TimeoutError, EmptyModelOutputError):
                if retry_index >= profile.timeout_retries:
                    raise
        raise RuntimeError("unreachable llm retry state")


class EmptyModelOutputError(ValueError):
    pass


def _completion_result(response: dict[str, Any]) -> LlmCompletionResult:
    choices = response.get("choices")
    if not choices:
        raise ValueError("llm response did not include choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("llm response did not include message content")
    if not content.strip():
        raise EmptyModelOutputError("llm response did not include non-empty message content")
    usage = response.get("usage")
    prompt_tokens = _int_or_none(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
    completion_tokens = (
        _int_or_none(usage.get("completion_tokens")) if isinstance(usage, dict) else None
    )
    total_tokens = _int_or_none(usage.get("total_tokens")) if isinstance(usage, dict) else None
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    cached_tokens = (
        _int_or_none(details.get("cached_tokens")) if isinstance(details, dict) else None
    )
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


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _completion_url(base_url: str) -> str:
    validate_base_url(base_url)
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def _connection_error_message(url: str, exc: urllib.error.URLError) -> str:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, FileNotFoundError):
        return (
            f"cannot reach llm endpoint {url}: local SSL certificate or proxy path "
            f"is missing ({reason}). Check SSL_CERT_FILE, SSL_CERT_DIR, "
            "HTTP_PROXY/HTTPS_PROXY, and the Python interpreter used to start the backend."
        )
    return f"cannot reach llm endpoint {url}: {reason}"


def _http_error_message(url: str, exc: urllib.error.HTTPError) -> str:
    return f"llm endpoint returned HTTP {exc.code} for {url}: {exc.reason}"


def _urllib_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
