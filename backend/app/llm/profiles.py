from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlparse


ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must start with http:// or https://")


def normalize_reasoning_effort(value: object) -> ReasoningEffort:
    if not isinstance(value, str):
        raise ValueError("reasoning_effort must be a string")
    normalized = value.strip().lower()
    if normalized not in REASONING_EFFORTS:
        allowed = ", ".join(REASONING_EFFORTS)
        raise ValueError(f"reasoning_effort must be one of: {allowed}")
    return cast(ReasoningEffort, normalized)


@dataclass(frozen=True)
class LlmProfileInput:
    name: str
    base_url: str
    api_key: str
    model: str
    reasoning_effort: ReasoningEffort
    timeout: float
    timeout_retries: int = 5

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("profile name is required")
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        validate_base_url(self.base_url)
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        object.__setattr__(
            self,
            "reasoning_effort",
            normalize_reasoning_effort(self.reasoning_effort),
        )
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.timeout_retries < 0:
            raise ValueError("timeout_retries must be non-negative")


@dataclass(frozen=True)
class LlmProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    reasoning_effort: ReasoningEffort
    timeout: float
    created_at: str
    updated_at: str
    timeout_retries: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reasoning_effort",
            normalize_reasoning_effort(self.reasoning_effort),
        )
        if self.id == "unconfigured":
            if self.timeout_retries < 0:
                raise ValueError("timeout_retries must be non-negative")
            return
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        validate_base_url(self.base_url)
        if self.timeout_retries < 0:
            raise ValueError("timeout_retries must be non-negative")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key_masked": mask_api_key(self.api_key),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "timeout": self.timeout,
            "timeout_retries": self.timeout_retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
