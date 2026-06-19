from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    MERLIN = "merlin"
    PERCIVAL = "percival"
    ASSASSIN = "assassin"
    MORGANA = "morgana"
    MORDRED = "mordred"
    OBERON = "oberon"
    MINION = "minion"
    LOYAL_SERVANT = "loyal_servant"
    TRISTAN = "tristan"
    ISOLDE = "isolde"
    UNKNOWN_EVIL = "unknown_evil"
    UNKNOWN_MERLIN = "unknown_merlin"


class Faction(str, Enum):
    GOOD = "good"
    EVIL = "evil"


class Phase(str, Enum):
    TEAM_PROPOSAL = "team_proposal"
    SPEECH = "speech"
    VOTING = "voting"
    QUEST = "quest"
    LADY_OF_LAKE = "lady_of_lake"
    ASSASSINATION = "assassination"
    COMPLETE = "complete"


class Vote(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class MissionAction(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"


class GameOption(str, Enum):
    LADY_OF_LAKE = "lady_of_lake"
    TRISTAN_ISOLDE = "tristan_isolde"
    ROLE_TIP_DETAIL = "role_tip_detail"


@dataclass(frozen=True)
class Player:
    id: str
    seat_index: int
    name: str
    is_human: bool
    role: Role
    faction: Faction
    original_name: str | None = None
    llm_profile_id: str | None = None


@dataclass(frozen=True)
class MissionConfig:
    round_number: int
    team_size: int
    fail_cards_required: int


@dataclass(frozen=True)
class LadyOfLakeInspection:
    viewer_player_id: str
    target_player_id: str
    target_faction: Faction
    round_number: int


@dataclass(frozen=True)
class GameState:
    players: tuple[Player, ...]
    missions: tuple[MissionConfig, ...]
    enabled_options: frozenset[GameOption] = field(default_factory=frozenset)
    current_round: int = 1
    leader_index: int = 0
    phase: Phase = Phase.TEAM_PROPOSAL
    proposed_team: tuple[str, ...] = ()
    speech_order: tuple[str, ...] = ()
    speeches: dict[str, str] = field(default_factory=dict)
    votes: dict[str, Vote] = field(default_factory=dict)
    quest_actions: dict[str, MissionAction] = field(default_factory=dict)
    quest_results: tuple[bool, ...] = ()
    failed_team_votes: int = 0
    forced_team: bool = False
    winner: Faction | None = None
    assassination_target_id: str | None = None
    lady_of_lake_holder_player_id: str | None = None
    lady_of_lake_previous_holder_ids: tuple[str, ...] = ()
    lady_of_lake_inspections: tuple[LadyOfLakeInspection, ...] = ()


@dataclass(frozen=True)
class PrivateView:
    viewer_player_id: str
    players: tuple[Player, ...]
    visible_roles: dict[str, Role | None]
    known_evil_player_ids: list[str]
    merlin_candidate_player_ids: list[str] = field(default_factory=list)
    known_good_player_ids: list[str] = field(default_factory=list)
    lady_of_lake_known_factions: dict[str, Faction] = field(default_factory=dict)

