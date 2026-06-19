from __future__ import annotations

from dataclasses import dataclass

from backend.app.game.models import MissionAction, Vote


@dataclass(frozen=True)
class TeamProposalDecision:
    team: tuple[str, ...]
    private_reason_summary: str
    public_message: str = ""


@dataclass(frozen=True)
class SpeechDecision:
    public_message: str
    stance: str
    private_reason_summary: str


@dataclass(frozen=True)
class VoteDecision:
    vote: Vote
    private_reason_summary: str
    public_reason: str = ""


@dataclass(frozen=True)
class MissionActionDecision:
    mission_action: MissionAction
    private_reason_summary: str


@dataclass(frozen=True)
class AssassinationDecision:
    target_player_id: str
    private_reason_summary: str
    candidate_ranking: tuple[str, ...]


@dataclass(frozen=True)
class LadyOfLakeDecision:
    target_player_id: str
    private_reason_summary: str
