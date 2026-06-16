import unittest

from backend.app.ai.strategy import FallbackStrategy
from backend.app.game.models import Faction, MissionAction, Role, Vote
from backend.app.game.rules import create_five_player_game, propose_team


class FallbackStrategyTests(unittest.TestCase):
    def test_team_proposal_has_required_size_and_includes_leader(self):
        state = create_five_player_game(seed=10)
        leader = state.players[state.leader_index]

        decision = FallbackStrategy().propose_team(state, leader.id)

        self.assertEqual(len(decision.team), 2)
        self.assertIn(leader.id, decision.team)

    def test_good_players_always_submit_success(self):
        state = create_five_player_game(seed=11)
        good_player = next(player for player in state.players if player.faction == Faction.GOOD)
        state = propose_team(state, state.players[0].id, (good_player.id, state.players[0].id))

        decision = FallbackStrategy().mission_action(state, good_player.id)

        self.assertEqual(decision.mission_action, MissionAction.SUCCESS)

    def test_evil_players_can_fail_quest(self):
        state = create_five_player_game(seed=12)
        evil_player = next(player for player in state.players if player.faction == Faction.EVIL)
        teammate = next(player for player in state.players if player.id != evil_player.id)
        state = propose_team(state, state.players[0].id, (evil_player.id, teammate.id))

        decision = FallbackStrategy().mission_action(state, evil_player.id)

        self.assertEqual(decision.mission_action, MissionAction.FAIL)

    def test_vote_approves_team_containing_self_and_rejects_otherwise(self):
        state = create_five_player_game(seed=13)
        voter = state.players[3]
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))

        decision = FallbackStrategy().vote(state, voter.id)

        self.assertEqual(decision.vote, Vote.REJECT)

        state_with_self = propose_team(
            create_five_player_game(seed=13),
            "player_1",
            ("player_1", voter.id),
        )
        self.assertEqual(FallbackStrategy().vote(state_with_self, voter.id).vote, Vote.APPROVE)

    def test_assassin_targets_non_evil_player(self):
        state = create_five_player_game(seed=14)
        assassin = next(player for player in state.players if player.role == Role.ASSASSIN)

        decision = FallbackStrategy().assassinate(state, assassin.id)
        target = next(player for player in state.players if player.id == decision.target_player_id)

        self.assertNotEqual(target.id, assassin.id)
        self.assertEqual(target.faction, Faction.GOOD)
