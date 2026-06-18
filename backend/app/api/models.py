from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.llm.profiles import LlmProfileInput


@dataclass(frozen=True)
class CreateGameRequest:
    human_name: str | None = None
    ai_names: list[str] | None = None
    default_llm_profile_id: str | None = None
    ai_profile_overrides: dict[str, str | None] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CreateGameRequest":
        human_name = _optional_string(payload.get("human_name"))
        default_profile = _optional_string(payload.get("default_llm_profile_id"))
        ai_names = payload.get("ai_names")
        overrides = payload.get("ai_profile_overrides")
        return cls(
            human_name=human_name,
            ai_names=[str(name) for name in ai_names] if isinstance(ai_names, list) else None,
            default_llm_profile_id=default_profile,
            ai_profile_overrides=_string_map(overrides) if isinstance(overrides, dict) else None,
        )


@dataclass(frozen=True)
class HumanActionRequest:
    action_type: str
    payload: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HumanActionRequest":
        action_type = _required_string(payload, "action_type")
        return cls(
            action_type=action_type,
            payload={key: value for key, value in payload.items() if key != "action_type"},
        )


@dataclass(frozen=True)
class LlmProfileRequest:
    name: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    timeout: float
    timeout_retries: int = 5

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LlmProfileRequest":
        return cls(
            name=_required_string(payload, "name"),
            base_url=_required_string(payload, "base_url"),
            api_key=str(payload.get("api_key", "")),
            model=_required_string(payload, "model"),
            temperature=float(payload["temperature"]),
            timeout=float(payload["timeout"]),
            timeout_retries=int(payload.get("timeout_retries", 5)),
        )

    def to_input(self) -> LlmProfileInput:
        return LlmProfileInput(
            name=self.name,
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            temperature=self.temperature,
            timeout=self.timeout,
            timeout_retries=self.timeout_retries,
        )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_map(payload: dict[Any, Any]) -> dict[str, str | None]:
    return {str(key): _optional_string(value) for key, value in payload.items()}
