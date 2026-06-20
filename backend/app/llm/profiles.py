from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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


@dataclass(frozen=True)
class LlmProfileInput:
    name: str
    base_url: str
    api_key: str
    model: str
    temperature: float
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
        if self.temperature < 0 or self.temperature > 2:
            raise ValueError("temperature must be between 0 and 2")
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
    temperature: float
    timeout: float
    created_at: str
    updated_at: str
    timeout_retries: int = 5

    def __post_init__(self) -> None:
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
            "temperature": self.temperature,
            "timeout": self.timeout,
            "timeout_retries": self.timeout_retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
