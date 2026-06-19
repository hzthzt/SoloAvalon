from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.game.models import GameOption
from backend.app.llm.profiles import LlmProfileInput


@dataclass(frozen=True)
class CreateGameRequest:
    player_count: int = 5
    enabled_options: frozenset[GameOption] = frozenset()
    human_name: str | None = None
    ai_names: list[str] | None = None
    default_llm_profile_id: str | None = None
    ai_profile_overrides: dict[str, str | None] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CreateGameRequest":
        human_name = _optional_string(payload.get("human_name"))
        player_count = _optional_player_count(payload.get("player_count"))
        enabled_options = _optional_enabled_options(payload.get("enabled_options"))
        _validate_options_for_player_count(player_count, enabled_options)
        default_profile = _optional_string(payload.get("default_llm_profile_id"))
        ai_names = payload.get("ai_names")
        overrides = payload.get("ai_profile_overrides")
        return cls(
            player_count=player_count,
            enabled_options=enabled_options,
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


def _optional_player_count(value: Any) -> int:
    if value is None:
        return 5
    try:
        player_count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("player_count must be between 5 and 10") from exc
    if player_count < 5 or player_count > 10:
        raise ValueError("player_count must be between 5 and 10")
    return player_count


def _optional_enabled_options(value: Any) -> frozenset[GameOption]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ValueError("enabled_options must be a list")
    options: set[GameOption] = set()
    for item in value:
        try:
            options.add(GameOption(str(item)))
        except ValueError as exc:
            raise ValueError(f"unknown game option: {item}") from exc
    return frozenset(options)


def _validate_options_for_player_count(
    player_count: int,
    enabled_options: frozenset[GameOption],
) -> None:
    if GameOption.LADY_OF_LAKE in enabled_options and player_count < 8:
        raise ValueError("lady_of_lake is only available for 8 to 10 players")
    if GameOption.TRISTAN_ISOLDE in enabled_options and player_count < 9:
        raise ValueError("tristan_isolde is only available for 9 to 10 players")


def _string_map(payload: dict[Any, Any]) -> dict[str, str | None]:
    return {str(key): _optional_string(value) for key, value in payload.items()}
