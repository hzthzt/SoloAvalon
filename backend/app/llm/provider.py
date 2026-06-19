from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

from .profiles import LlmProfile


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class LlmProvider:
    def __init__(self, transport: Transport | None = None):
        self._transport = transport or _urllib_transport

    def chat_completion(
        self,
        profile: LlmProfile,
        messages: list[dict[str, str]],
    ) -> str:
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
                return _completion_content(response)
            except (TimeoutError, EmptyModelOutputError):
                if retry_index >= profile.timeout_retries:
                    raise
        raise RuntimeError("unreachable llm retry state")


class EmptyModelOutputError(ValueError):
    pass


def _completion_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not choices:
        raise ValueError("llm response did not include choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("llm response did not include message content")
    if not content.strip():
        raise EmptyModelOutputError("llm response did not include non-empty message content")
    return content


def _completion_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


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
