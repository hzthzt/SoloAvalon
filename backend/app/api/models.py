from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from pydantic import BaseModel

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
    expected_revision: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HumanActionRequest":
        action_type = _required_string(payload, "action_type")
        return cls(
            action_type=action_type,
            expected_revision=expected_revision(payload),
            payload={key: value for key, value in payload.items() if key not in {"action_type", "expected_revision"}},
        )


def expected_revision(payload: dict[str, Any]) -> int:
    value = payload.get("expected_revision")
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    return value


class PublicEventView(BaseModel):
    id: int
    game_id: str
    event_index: int
    event_type: str
    public_payload: dict[str, Any]
    created_at: str


class PlayerView(BaseModel):
    id: str
    seat_index: int
    name: str
    original_name: str | None
    is_human: bool
    visible_role: str | None
    revealed_role: str | None


class MissionView(BaseModel):
    round_number: int
    team_size: int
    fail_cards_required: int


class PublicErrorView(BaseModel):
    code: str
    error_type: str
    category: str
    message: str
    suggestion: str


class GameView(BaseModel):
    id: str
    display_name: str
    status: str
    revision: int
    is_advancing: bool
    pause_error: PublicErrorView | None = None
    player_count: int
    phase: str
    current_round: int
    leader_player_id: str
    missions: list[MissionView]
    enabled_options: list[str]
    human_player_id: str
    human_role: str
    human_faction: str
    known_evil_player_ids: list[str]
    lady_of_lake_holder_player_id: str | None
    lady_of_lake_previous_holder_ids: list[str]
    lady_of_lake_eligible_target_ids: list[str]
    lady_of_lake_known_factions: dict[str, str]
    players: list[PlayerView]
    proposed_team: list[str]
    speech_order: list[str]
    speeches: dict[str, str]
    votes_cast_count: int
    quest_actions_submitted_count: int
    quest_results: list[str]
    failed_team_votes: int
    forced_team: bool
    winner: str | None
    assassination_target_id: str | None
    next_human_action: str | None
    events: list[PublicEventView]


class GameSummaryView(BaseModel):
    id: str
    display_name: str
    status: str
    revision: int
    player_count: int
    enabled_options: list[str]
    current_round: int
    current_phase: str
    winner: str | None
    archived_at: str | None
    created_at: str
    updated_at: str


class AuditEventView(PublicEventView):
    private_payload: dict[str, Any] | None


class RoomReview(BaseModel):
    game: GameView
    events: list[AuditEventView]
    ai_decisions: list[dict[str, Any]]
    usage_by_player: list[dict[str, Any]]
    usage_by_model: list[dict[str, Any]]


class LlmProfileRequest(LlmProfileInput):
    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LlmProfileRequest":
        return cls.model_validate(payload)

    def to_input(self) -> LlmProfileInput:
        return self


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
