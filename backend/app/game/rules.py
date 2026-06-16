from __future__ import annotations

import random
from dataclasses import replace

from .models import (
    Faction,
    GameState,
    MissionAction,
    MissionConfig,
    Phase,
    Player,
    PrivateView,
    Role,
    Vote,
)


class InvalidActionError(ValueError):
    pass


STANDARD_FIVE_PLAYER_ROLES: tuple[Role, ...] = (
    Role.MERLIN,
    Role.ASSASSIN,
    Role.MINION,
    Role.LOYAL_SERVANT,
    Role.LOYAL_SERVANT,
)

FIVE_PLAYER_MISSIONS: tuple[MissionConfig, ...] = (
    MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
    MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
    MissionConfig(round_number=3, team_size=2, fail_cards_required=1),
    MissionConfig(round_number=4, team_size=3, fail_cards_required=1),
    MissionConfig(round_number=5, team_size=3, fail_cards_required=1),
)


def faction_for_role(role: Role) -> Faction:
    if role in {Role.MERLIN, Role.LOYAL_SERVANT}:
        return Faction.GOOD
    if role in {Role.ASSASSIN, Role.MINION}:
        return Faction.EVIL
    raise ValueError(f"{role.value} is not a playable role")


def create_five_player_game(
    seed: int | None = None,
    human_seat_index: int = 0,
) -> GameState:
    if human_seat_index < 0 or human_seat_index > 4:
        raise ValueError("human_seat_index must be between 0 and 4")

    roles = list(STANDARD_FIVE_PLAYER_ROLES)
    random.Random(seed).shuffle(roles)
    players = []
    ai_number = 1
    for seat_index, role in enumerate(roles):
        is_human = seat_index == human_seat_index
        name = "You" if is_human else f"AI {ai_number}"
        if not is_human:
            ai_number += 1
        players.append(
            Player(
                id=f"player_{seat_index + 1}",
                seat_index=seat_index,
                name=name,
                is_human=is_human,
                role=role,
                faction=faction_for_role(role),
            )
        )

    return GameState(players=tuple(players), missions=FIVE_PLAYER_MISSIONS)


def private_view_for_player(state: GameState, viewer_id: str) -> PrivateView:
    viewer = _player_by_id(state, viewer_id)
    visible_roles: dict[str, Role | None] = {player.id: None for player in state.players}
    visible_roles[viewer.id] = viewer.role
    known_evil_player_ids: list[str] = []

    if viewer.role == Role.MERLIN:
        for player in state.players:
            if player.faction == Faction.EVIL:
                visible_roles[player.id] = Role.UNKNOWN_EVIL
                known_evil_player_ids.append(player.id)
    elif viewer.faction == Faction.EVIL:
        for player in state.players:
            if player.faction == Faction.EVIL and player.id != viewer.id:
                visible_roles[player.id] = Role.UNKNOWN_EVIL
                known_evil_player_ids.append(player.id)

    return PrivateView(
        viewer_player_id=viewer.id,
        players=state.players,
        visible_roles=visible_roles,
        known_evil_player_ids=known_evil_player_ids,
    )


def propose_team(
    state: GameState,
    leader_player_id: str,
    team_player_ids: tuple[str, ...],
) -> GameState:
    if state.phase != Phase.TEAM_PROPOSAL:
        raise InvalidActionError("teams can only be proposed during team proposal phase")

    leader = state.players[state.leader_index]
    if leader.id != leader_player_id:
        raise InvalidActionError("only the current leader can propose a team")

    _validate_team(state, team_player_ids)
    if state.forced_team:
        return replace(
            state,
            phase=Phase.QUEST,
            proposed_team=tuple(team_player_ids),
            speech_order=(),
            speeches={},
            votes={},
            quest_actions={},
        )

    return replace(
        state,
        phase=Phase.SPEECH,
        proposed_team=tuple(team_player_ids),
        speech_order=_speech_order_from_current_leader(state),
        speeches={},
        votes={},
        quest_actions={},
    )


def record_speech(state: GameState, player_id: str, message: str) -> GameState:
    if state.phase != Phase.SPEECH:
        raise InvalidActionError("speeches can only be recorded during speech phase")
    _player_by_id(state, player_id)

    speech_index = len(state.speeches)
    if speech_index >= len(state.speech_order):
        raise InvalidActionError("all speeches have already been recorded")
    expected_player_id = state.speech_order[speech_index]
    if player_id != expected_player_id:
        raise InvalidActionError(f"next speaker must be {expected_player_id}")

    speeches = dict(state.speeches)
    speeches[player_id] = message
    next_phase = Phase.VOTING if len(speeches) == len(state.players) else Phase.SPEECH
    return replace(state, phase=next_phase, speeches=speeches)


def cast_vote(state: GameState, player_id: str, vote: Vote) -> GameState:
    if state.phase != Phase.VOTING:
        raise InvalidActionError("votes can only be cast during voting phase")
    _player_by_id(state, player_id)
    if player_id in state.votes:
        raise InvalidActionError("player has already voted")
    if not isinstance(vote, Vote):
        raise InvalidActionError("vote must be a Vote value")

    votes = dict(state.votes)
    votes[player_id] = vote
    return replace(state, votes=votes)


def finalize_vote(state: GameState) -> GameState:
    if state.phase != Phase.VOTING:
        raise InvalidActionError("votes can only be finalized during voting phase")
    if len(state.votes) != len(state.players):
        raise InvalidActionError("all players must vote before finalizing")

    approvals = sum(1 for vote in state.votes.values() if vote == Vote.APPROVE)
    if approvals > len(state.players) // 2:
        return replace(
            state,
            phase=Phase.QUEST,
            failed_team_votes=0,
            votes={},
            speeches={},
            speech_order=(),
            quest_actions={},
        )

    failed_team_votes = state.failed_team_votes + 1
    return replace(
        state,
        phase=Phase.TEAM_PROPOSAL,
        leader_index=(state.leader_index + 1) % len(state.players),
        proposed_team=(),
        speech_order=(),
        speeches={},
        votes={},
        quest_actions={},
        failed_team_votes=failed_team_votes,
        forced_team=failed_team_votes >= 5,
    )


def force_team_after_failed_votes(
    state: GameState,
    leader_player_id: str,
    team_player_ids: tuple[str, ...],
) -> GameState:
    if not state.forced_team:
        raise InvalidActionError("forced team is only available after five failed team votes")
    return propose_team(state, leader_player_id, team_player_ids)


def submit_quest_action(
    state: GameState,
    player_id: str,
    action: MissionAction,
) -> GameState:
    if state.phase != Phase.QUEST:
        raise InvalidActionError("quest actions can only be submitted during quest phase")
    player = _player_by_id(state, player_id)
    if player_id not in state.proposed_team:
        raise InvalidActionError("only quest team members can submit quest actions")
    if player_id in state.quest_actions:
        raise InvalidActionError("player has already submitted a quest action")
    if not isinstance(action, MissionAction):
        raise InvalidActionError("quest action must be a MissionAction value")
    if player.faction == Faction.GOOD and action == MissionAction.FAIL:
        raise InvalidActionError("good players cannot submit fail actions")

    quest_actions = dict(state.quest_actions)
    quest_actions[player_id] = action
    return replace(state, quest_actions=quest_actions)


def finalize_quest(state: GameState) -> GameState:
    if state.phase != Phase.QUEST:
        raise InvalidActionError("quests can only be finalized during quest phase")
    if len(state.quest_actions) != len(state.proposed_team):
        raise InvalidActionError("all quest members must submit actions before finalizing")

    mission = state.missions[state.current_round - 1]
    fail_cards = sum(
        1 for action in state.quest_actions.values() if action == MissionAction.FAIL
    )
    quest_succeeded = fail_cards < mission.fail_cards_required
    quest_results = state.quest_results + (quest_succeeded,)
    success_count = sum(1 for result in quest_results if result)
    failure_count = sum(1 for result in quest_results if not result)

    if failure_count >= 3:
        return replace(
            state,
            phase=Phase.COMPLETE,
            quest_results=quest_results,
            quest_actions={},
            winner=Faction.EVIL,
        )
    if success_count >= 3:
        return replace(
            state,
            phase=Phase.ASSASSINATION,
            quest_results=quest_results,
            quest_actions={},
            failed_team_votes=0,
            forced_team=False,
        )

    return replace(
        state,
        phase=Phase.TEAM_PROPOSAL,
        current_round=state.current_round + 1,
        leader_index=(state.leader_index + 1) % len(state.players),
        proposed_team=(),
        speech_order=(),
        speeches={},
        votes={},
        quest_actions={},
        quest_results=quest_results,
        failed_team_votes=0,
        forced_team=False,
    )


def assassinate(
    state: GameState,
    assassin_player_id: str,
    target_player_id: str,
) -> GameState:
    if state.phase != Phase.ASSASSINATION:
        raise InvalidActionError("assassination is only available during assassination phase")
    assassin = _player_by_id(state, assassin_player_id)
    target = _player_by_id(state, target_player_id)
    if assassin.role != Role.ASSASSIN:
        raise InvalidActionError("only the assassin can choose the assassination target")
    if target.id == assassin.id:
        raise InvalidActionError("assassin must target another player")

    winner = Faction.EVIL if target.role == Role.MERLIN else Faction.GOOD
    return replace(
        state,
        phase=Phase.COMPLETE,
        winner=winner,
        assassination_target_id=target.id,
    )


def _player_by_id(state: GameState, player_id: str) -> Player:
    for player in state.players:
        if player.id == player_id:
            return player
    raise ValueError(f"unknown player id: {player_id}")


def _validate_team(state: GameState, team_player_ids: tuple[str, ...]) -> None:
    if len(set(team_player_ids)) != len(team_player_ids):
        raise InvalidActionError("team cannot contain duplicate players")
    valid_player_ids = {player.id for player in state.players}
    unknown_ids = set(team_player_ids) - valid_player_ids
    if unknown_ids:
        raise InvalidActionError(f"unknown team player ids: {sorted(unknown_ids)}")

    mission = state.missions[state.current_round - 1]
    if len(team_player_ids) != mission.team_size:
        raise InvalidActionError(
            f"round {state.current_round} requires {mission.team_size} team members"
        )


def _speech_order_from_current_leader(state: GameState) -> tuple[str, ...]:
    return tuple(
        state.players[(state.leader_index + offset) % len(state.players)].id
        for offset in range(len(state.players))
    )
