from __future__ import annotations

from dataclasses import dataclass

from backend.app.game.models import Faction, GameState, MissionAction, Player, Role, Vote


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


class FallbackStrategy:
    def propose_team(self, state: GameState, leader_player_id: str) -> TeamProposalDecision:
        leader = _player_by_id(state, leader_player_id)
        team_size = state.missions[state.current_round - 1].team_size
        team = [leader.id]
        if leader.faction == Faction.EVIL:
            team.extend(player.id for player in state.players if player.faction == Faction.EVIL)
        team.extend(player.id for player in state.players)
        unique_team = _first_unique(team, team_size)
        return TeamProposalDecision(
            team=tuple(unique_team),
            private_reason_summary="fallback selects a legal team centered on the leader",
            public_message="I want a clean baseline team for this quest.",
        )

    def speak(self, state: GameState, player_id: str) -> SpeechDecision:
        stance = "support_team" if player_id in state.proposed_team else "uncertain"
        if stance == "support_team":
            message = "I can support this team and want to see the vote data."
        else:
            message = "I am not fully sold yet, but the vote pattern will be useful."
        return SpeechDecision(
            public_message=message,
            stance=stance,
            private_reason_summary="fallback keeps the public statement short and non-revealing",
        )

    def vote(self, state: GameState, player_id: str) -> VoteDecision:
        player = _player_by_id(state, player_id)
        if state.forced_team or player_id in state.proposed_team:
            vote = Vote.APPROVE
        elif player.faction == Faction.EVIL and _team_contains_evil(state):
            vote = Vote.APPROVE
        else:
            vote = Vote.REJECT
        return VoteDecision(
            vote=vote,
            private_reason_summary="fallback votes by self-interest and visible team membership",
            public_reason="Team membership makes this vote acceptable." if vote == Vote.APPROVE else "I prefer another team.",
        )

    def mission_action(self, state: GameState, player_id: str) -> MissionActionDecision:
        player = _player_by_id(state, player_id)
        action = MissionAction.FAIL if player.faction == Faction.EVIL else MissionAction.SUCCESS
        return MissionActionDecision(
            mission_action=action,
            private_reason_summary="fallback follows faction-legal quest action",
        )

    def assassinate(self, state: GameState, assassin_player_id: str) -> AssassinationDecision:
        assassin = _player_by_id(state, assassin_player_id)
        if assassin.role != Role.ASSASSIN:
            raise ValueError("fallback assassination requires the assassin player")
        candidates = [
            player.id
            for player in state.players
            if player.faction == Faction.GOOD and player.id != assassin.id
        ]
        if not candidates:
            candidates = [player.id for player in state.players if player.id != assassin.id]
        return AssassinationDecision(
            target_player_id=candidates[0],
            private_reason_summary="fallback targets the first legal good-side candidate",
            candidate_ranking=tuple(candidates),
        )


def _player_by_id(state: GameState, player_id: str) -> Player:
    for player in state.players:
        if player.id == player_id:
            return player
    raise ValueError(f"unknown player id: {player_id}")


def _team_contains_evil(state: GameState) -> bool:
    by_id = {player.id: player for player in state.players}
    return any(by_id[player_id].faction == Faction.EVIL for player_id in state.proposed_team)


def _first_unique(player_ids: list[str], size: int) -> list[str]:
    selected: list[str] = []
    for player_id in player_ids:
        if player_id not in selected:
            selected.append(player_id)
        if len(selected) == size:
            return selected
    return selected
