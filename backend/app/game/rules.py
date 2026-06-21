from __future__ import annotations

import random
from dataclasses import replace

from .models import (
    Faction,
    GameOption,
    GameState,
    LadyOfLakeInspection,
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
    Role.PERCIVAL,
    Role.LOYAL_SERVANT,
    Role.ASSASSIN,
    Role.MORGANA,
)

STANDARD_ROLE_SETUPS: dict[int, tuple[Role, ...]] = {
    5: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
    ),
    6: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
    ),
    7: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
        Role.OBERON,
    ),
    8: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
        Role.MINION,
    ),
    9: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
        Role.MORDRED,
    ),
    10: (
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.LOYAL_SERVANT,
        Role.ASSASSIN,
        Role.MORGANA,
        Role.MORDRED,
        Role.OBERON,
    ),
}

STANDARD_MISSION_CONFIGS: dict[int, tuple[MissionConfig, ...]] = {
    5: (
        MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=2, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=5, team_size=3, fail_cards_required=1),
    ),
    6: (
        MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=5, team_size=4, fail_cards_required=1),
    ),
    7: (
        MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=4, fail_cards_required=2),
        MissionConfig(round_number=5, team_size=4, fail_cards_required=1),
    ),
    8: (
        MissionConfig(round_number=1, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=5, fail_cards_required=2),
        MissionConfig(round_number=5, team_size=5, fail_cards_required=1),
    ),
    9: (
        MissionConfig(round_number=1, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=5, fail_cards_required=2),
        MissionConfig(round_number=5, team_size=5, fail_cards_required=1),
    ),
    10: (
        MissionConfig(round_number=1, team_size=3, fail_cards_required=1),
        MissionConfig(round_number=2, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=3, team_size=4, fail_cards_required=1),
        MissionConfig(round_number=4, team_size=5, fail_cards_required=2),
        MissionConfig(round_number=5, team_size=5, fail_cards_required=1),
    ),
}

STANDARD_FACTION_COUNTS: dict[int, tuple[int, int]] = {
    5: (3, 2),
    6: (4, 2),
    7: (4, 3),
    8: (5, 3),
    9: (6, 3),
    10: (6, 4),
}

FIVE_PLAYER_MISSIONS: tuple[MissionConfig, ...] = STANDARD_MISSION_CONFIGS[5]


def faction_for_role(role: Role) -> Faction:
    if role in {
        Role.MERLIN,
        Role.PERCIVAL,
        Role.LOYAL_SERVANT,
        Role.TRISTAN,
        Role.ISOLDE,
    }:
        return Faction.GOOD
    if role in {
        Role.ASSASSIN,
        Role.MORGANA,
        Role.MORDRED,
        Role.OBERON,
        Role.MINION,
    }:
        return Faction.EVIL
    raise ValueError(f"{role.value} is not a playable role")


def create_five_player_game(
    seed: int | None = None,
    human_seat_index: int | None = None,
    human_name: str = "真人玩家",
    ai_names: list[str] | None = None,
) -> GameState:
    return create_game(
        player_count=5,
        seed=seed,
        human_seat_index=human_seat_index,
        human_name=human_name,
        ai_names=ai_names,
    )


def create_game(
    player_count: int = 5,
    seed: int | None = None,
    human_seat_index: int | None = None,
    human_name: str = "真人玩家",
    ai_names: list[str] | None = None,
    enabled_options: set[GameOption | str] | frozenset[GameOption | str] | None = None,
) -> GameState:
    if player_count not in STANDARD_MISSION_CONFIGS:
        raise ValueError("player_count must be between 5 and 10")

    normalized_options = _normalize_enabled_options(enabled_options)
    _validate_options_for_player_count(player_count, normalized_options)

    rng = random.Random(seed)
    if human_seat_index is None:
        human_seat_index = rng.randrange(player_count)
    if human_seat_index < 0 or human_seat_index >= player_count:
        raise ValueError(f"human_seat_index must be between 0 and {player_count - 1}")

    roles = list(_role_setup_for_game(player_count, normalized_options))
    rng.shuffle(roles)
    configured_ai_names = list(ai_names or [])
    ai_original_names = [
        (
            configured_ai_names[index].strip()
            if index < len(configured_ai_names) and configured_ai_names[index].strip()
            else f"AI {index + 1}"
        )
        for index in range(player_count - 1)
    ]
    rng.shuffle(ai_original_names)
    players = []
    ai_index = 0
    for seat_index, role in enumerate(roles):
        is_human = seat_index == human_seat_index
        if is_human:
            original_name = human_name.strip() or "真人玩家"
        else:
            original_name = ai_original_names[ai_index]
            ai_index += 1
        players.append(
            Player(
                id=f"player_{seat_index + 1}",
                seat_index=seat_index,
                name=f"玩家{seat_index + 1}",
                is_human=is_human,
                role=role,
                faction=faction_for_role(role),
                original_name=original_name,
            )
        )

    lake_holder = None
    lake_previous_holders: tuple[str, ...] = ()
    if GameOption.LADY_OF_LAKE in normalized_options:
        lake_holder = f"player_{player_count}"
        lake_previous_holders = (lake_holder,)

    return GameState(
        players=tuple(players),
        missions=STANDARD_MISSION_CONFIGS[player_count],
        enabled_options=normalized_options,
        lady_of_lake_holder_player_id=lake_holder,
        lady_of_lake_previous_holder_ids=lake_previous_holders,
    )


def private_view_for_player(state: GameState, viewer_id: str) -> PrivateView:
    viewer = _player_by_id(state, viewer_id)
    visible_roles: dict[str, Role | None] = {player.id: None for player in state.players}
    visible_roles[viewer.id] = viewer.role
    known_evil_player_ids: list[str] = []
    merlin_candidate_player_ids: list[str] = []
    known_good_player_ids: list[str] = []
    lady_of_lake_known_factions: dict[str, Faction] = {
        inspection.target_player_id: inspection.target_faction
        for inspection in state.lady_of_lake_inspections
        if inspection.viewer_player_id == viewer.id
    }

    if viewer.role == Role.MERLIN:
        for player in state.players:
            if player.faction == Faction.EVIL and player.role != Role.MORDRED:
                visible_roles[player.id] = Role.UNKNOWN_EVIL
                known_evil_player_ids.append(player.id)
    elif viewer.role == Role.PERCIVAL:
        for player in state.players:
            if player.role in {Role.MERLIN, Role.MORGANA}:
                visible_roles[player.id] = Role.UNKNOWN_MERLIN
                merlin_candidate_player_ids.append(player.id)
    elif viewer.faction == Faction.EVIL and viewer.role != Role.OBERON:
        for player in state.players:
            if (
                player.faction == Faction.EVIL
                and player.id != viewer.id
                and player.role != Role.OBERON
            ):
                visible_roles[player.id] = Role.UNKNOWN_EVIL
                known_evil_player_ids.append(player.id)
    elif viewer.role in {Role.TRISTAN, Role.ISOLDE}:
        partner_role = Role.ISOLDE if viewer.role == Role.TRISTAN else Role.TRISTAN
        for player in state.players:
            if player.role == partner_role:
                visible_roles[player.id] = partner_role
                known_good_player_ids.append(player.id)

    return PrivateView(
        viewer_player_id=viewer.id,
        players=state.players,
        visible_roles=visible_roles,
        known_evil_player_ids=known_evil_player_ids,
        merlin_candidate_player_ids=merlin_candidate_player_ids,
        known_good_player_ids=known_good_player_ids,
        lady_of_lake_known_factions=lady_of_lake_known_factions,
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

    next_phase = (
        Phase.LADY_OF_LAKE
        if _should_enter_lady_of_lake_phase(state, quest_results)
        else Phase.TEAM_PROPOSAL
    )
    return replace(
        state,
        phase=next_phase,
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


def eligible_lady_of_lake_target_ids(state: GameState) -> tuple[str, ...]:
    holder_id = state.lady_of_lake_holder_player_id
    if holder_id is None:
        return ()
    previous_holders = set(state.lady_of_lake_previous_holder_ids)
    return tuple(
        player.id
        for player in state.players
        if player.id != holder_id and player.id not in previous_holders
    )


def use_lady_of_lake(
    state: GameState,
    viewer_player_id: str,
    target_player_id: str,
) -> GameState:
    if state.phase != Phase.LADY_OF_LAKE:
        raise InvalidActionError("lady of the lake can only be used during lady phase")
    if state.lady_of_lake_holder_player_id != viewer_player_id:
        raise InvalidActionError("only the current lady of the lake holder can inspect")
    target = _player_by_id(state, target_player_id)
    if target_player_id == viewer_player_id:
        raise InvalidActionError("lady of the lake holder cannot inspect themselves")
    if target_player_id in state.lady_of_lake_previous_holder_ids:
        raise InvalidActionError("lady of the lake cannot inspect a previous holder")

    inspection = LadyOfLakeInspection(
        viewer_player_id=viewer_player_id,
        target_player_id=target.id,
        target_faction=target.faction,
        round_number=len(state.quest_results),
    )
    return replace(
        state,
        phase=Phase.TEAM_PROPOSAL,
        lady_of_lake_holder_player_id=target.id,
        lady_of_lake_previous_holder_ids=state.lady_of_lake_previous_holder_ids
        + (target.id,),
        lady_of_lake_inspections=state.lady_of_lake_inspections + (inspection,),
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


def _normalize_enabled_options(
    enabled_options: set[GameOption | str] | frozenset[GameOption | str] | None,
) -> frozenset[GameOption]:
    if not enabled_options:
        return frozenset()
    return frozenset(
        option if isinstance(option, GameOption) else GameOption(str(option))
        for option in enabled_options
    )


def _validate_options_for_player_count(
    player_count: int,
    enabled_options: frozenset[GameOption],
) -> None:
    if GameOption.LADY_OF_LAKE in enabled_options and player_count < 8:
        raise ValueError("lady_of_lake is only available for 8 to 10 players")
    if GameOption.TRISTAN_ISOLDE in enabled_options and player_count < 9:
        raise ValueError("tristan_isolde is only available for 9 to 10 players")


def _role_setup_for_game(
    player_count: int,
    enabled_options: frozenset[GameOption],
) -> tuple[Role, ...]:
    roles = list(STANDARD_ROLE_SETUPS[player_count])
    if GameOption.TRISTAN_ISOLDE in enabled_options:
        loyal_indexes = [
            index for index, role in enumerate(roles) if role == Role.LOYAL_SERVANT
        ]
        if len(loyal_indexes) < 2:
            raise ValueError("tristan_isolde requires at least two loyal servants")
        roles[loyal_indexes[0]] = Role.TRISTAN
        roles[loyal_indexes[1]] = Role.ISOLDE
    return tuple(roles)


def _should_enter_lady_of_lake_phase(
    state: GameState,
    quest_results: tuple[bool, ...],
) -> bool:
    return (
        GameOption.LADY_OF_LAKE in state.enabled_options
        and len(quest_results) in {2, 3, 4}
        and state.lady_of_lake_holder_player_id is not None
    )


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
