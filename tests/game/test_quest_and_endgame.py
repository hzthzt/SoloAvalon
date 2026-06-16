import unittest

from backend.app.game.models import Faction, MissionAction, Phase, Role, Vote
from backend.app.game.rules import (
    InvalidActionError,
    assassinate,
    cast_vote,
    create_five_player_game,
    finalize_quest,
    finalize_vote,
    propose_team,
    record_speech,
    submit_quest_action,
)


def players_with_faction(state, faction):
    return [player for player in state.players if player.faction == faction]


def player_with_role(state, role):
    return next(player for player in state.players if player.role == role)


def approve_team(state, team):
    leader = state.players[state.leader_index]
    state = propose_team(state, leader.id, tuple(player.id for player in team))
    for player_id in state.speech_order:
        state = record_speech(state, player_id, f"{player_id} supports the test team")
    for player in state.players:
        state = cast_vote(state, player.id, Vote.APPROVE)
    return finalize_vote(state)


def complete_failed_quest(state):
    team_size = state.missions[state.current_round - 1].team_size
    evil = players_with_faction(state, Faction.EVIL)[0]
    good_players = players_with_faction(state, Faction.GOOD)
    team = [evil] + good_players[: team_size - 1]
    state = approve_team(state, team)
    for player in team:
        action = MissionAction.FAIL if player.id == evil.id else MissionAction.SUCCESS
        state = submit_quest_action(state, player.id, action)
    return finalize_quest(state)


def complete_successful_quest(state):
    team_size = state.missions[state.current_round - 1].team_size
    team = state.players[:team_size]
    state = approve_team(state, team)
    for player in team:
        state = submit_quest_action(state, player.id, MissionAction.SUCCESS)
    return finalize_quest(state)


class QuestAndEndgameTests(unittest.TestCase):
    def test_only_quest_members_can_submit_actions(self):
        state = create_five_player_game(seed=21)
        team = state.players[:2]
        state = approve_team(state, team)
        non_member = next(player for player in state.players if player.id not in state.proposed_team)

        with self.assertRaises(InvalidActionError):
            submit_quest_action(state, non_member.id, MissionAction.SUCCESS)

    def test_good_players_cannot_submit_fail_actions(self):
        state = create_five_player_game(seed=22)
        good_players = players_with_faction(state, Faction.GOOD)
        state = approve_team(state, good_players[:2])

        with self.assertRaises(InvalidActionError):
            submit_quest_action(state, good_players[0].id, MissionAction.FAIL)

    def test_one_fail_card_fails_a_five_player_quest(self):
        state = create_five_player_game(seed=23)

        state = complete_failed_quest(state)

        self.assertEqual(state.quest_results, (False,))
        self.assertEqual(state.current_round, 2)
        self.assertEqual(state.phase, Phase.TEAM_PROPOSAL)

    def test_three_failed_quests_end_with_evil_victory(self):
        state = create_five_player_game(seed=24)

        for _ in range(3):
            state = complete_failed_quest(state)

        self.assertEqual(state.phase, Phase.COMPLETE)
        self.assertEqual(state.winner, Faction.EVIL)
        self.assertEqual(state.quest_results, (False, False, False))

    def test_three_successful_quests_enter_assassination(self):
        state = create_five_player_game(seed=25)

        for _ in range(3):
            state = complete_successful_quest(state)

        self.assertEqual(state.phase, Phase.ASSASSINATION)
        self.assertIsNone(state.winner)
        self.assertEqual(state.quest_results, (True, True, True))

    def test_assassin_killing_merlin_gives_evil_victory(self):
        state = create_five_player_game(seed=26)
        for _ in range(3):
            state = complete_successful_quest(state)
        assassin = player_with_role(state, Role.ASSASSIN)
        merlin = player_with_role(state, Role.MERLIN)

        state = assassinate(state, assassin.id, merlin.id)

        self.assertEqual(state.phase, Phase.COMPLETE)
        self.assertEqual(state.winner, Faction.EVIL)
        self.assertEqual(state.assassination_target_id, merlin.id)

    def test_assassin_missing_merlin_gives_good_victory(self):
        state = create_five_player_game(seed=27)
        for _ in range(3):
            state = complete_successful_quest(state)
        assassin = player_with_role(state, Role.ASSASSIN)
        non_merlin_target = next(
            player
            for player in state.players
            if player.role not in {Role.MERLIN, Role.ASSASSIN}
        )

        state = assassinate(state, assassin.id, non_merlin_target.id)

        self.assertEqual(state.phase, Phase.COMPLETE)
        self.assertEqual(state.winner, Faction.GOOD)
        self.assertEqual(state.assassination_target_id, non_merlin_target.id)

