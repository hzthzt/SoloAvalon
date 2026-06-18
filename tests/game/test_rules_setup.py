import unittest

from backend.app.game.models import Faction, Phase, Role
from backend.app.game.rules import (
    STANDARD_FACTION_COUNTS,
    STANDARD_MISSION_CONFIGS,
    create_five_player_game,
    private_view_for_player,
)


def player_with_role(state, role):
    return next(player for player in state.players if player.role == role)


class RulesSetupTests(unittest.TestCase):
    def test_standard_mission_and_faction_configs_cover_five_to_ten_players(self):
        self.assertEqual(
            {
                player_count: [
                    (mission.team_size, mission.fail_cards_required)
                    for mission in missions
                ]
                for player_count, missions in STANDARD_MISSION_CONFIGS.items()
            },
            {
                5: [(2, 1), (3, 1), (2, 1), (3, 1), (3, 1)],
                6: [(2, 1), (3, 1), (4, 1), (3, 1), (4, 1)],
                7: [(2, 1), (3, 1), (3, 1), (4, 2), (4, 1)],
                8: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
                9: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
                10: [(3, 1), (4, 1), (4, 1), (5, 2), (5, 1)],
            },
        )
        self.assertEqual(
            STANDARD_FACTION_COUNTS,
            {
                5: (3, 2),
                6: (4, 2),
                7: (4, 3),
                8: (5, 3),
                9: (6, 3),
                10: (6, 4),
            },
        )

    def test_create_five_player_game_assigns_standard_roles(self):
        state = create_five_player_game(seed=20260615, human_seat_index=0)

        self.assertEqual(len(state.players), 5)
        self.assertCountEqual(
            [player.role for player in state.players],
            [
                Role.MERLIN,
                Role.ASSASSIN,
                Role.MINION,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
            ],
        )
        self.assertEqual(state.players[0].id, "player_1")
        self.assertTrue(state.players[0].is_human)
        self.assertEqual(state.players[0].name, "You")
        self.assertFalse(state.players[1].is_human)
        self.assertEqual(state.phase, Phase.TEAM_PROPOSAL)
        self.assertEqual(state.current_round, 1)
        self.assertEqual(state.leader_index, 0)
        self.assertEqual(
            [(mission.team_size, mission.fail_cards_required) for mission in state.missions],
            [(2, 1), (3, 1), (2, 1), (3, 1), (3, 1)],
        )

    def test_factions_derive_from_roles(self):
        state = create_five_player_game(seed=3)

        self.assertEqual(player_with_role(state, Role.MERLIN).faction, Faction.GOOD)
        self.assertEqual(player_with_role(state, Role.LOYAL_SERVANT).faction, Faction.GOOD)
        self.assertEqual(player_with_role(state, Role.ASSASSIN).faction, Faction.EVIL)
        self.assertEqual(player_with_role(state, Role.MINION).faction, Faction.EVIL)

    def test_merlin_private_view_knows_evil_players(self):
        state = create_five_player_game(seed=11)
        merlin = player_with_role(state, Role.MERLIN)
        assassin = player_with_role(state, Role.ASSASSIN)
        minion = player_with_role(state, Role.MINION)

        view = private_view_for_player(state, merlin.id)

        self.assertEqual(view.viewer_player_id, merlin.id)
        self.assertCountEqual(view.known_evil_player_ids, [assassin.id, minion.id])
        self.assertEqual(view.visible_roles[merlin.id], Role.MERLIN)
        self.assertEqual(view.visible_roles[assassin.id], Role.UNKNOWN_EVIL)
        self.assertEqual(view.visible_roles[minion.id], Role.UNKNOWN_EVIL)

    def test_evil_players_know_each_other_but_not_exact_roles(self):
        state = create_five_player_game(seed=13)
        assassin = player_with_role(state, Role.ASSASSIN)
        minion = player_with_role(state, Role.MINION)

        assassin_view = private_view_for_player(state, assassin.id)
        minion_view = private_view_for_player(state, minion.id)

        self.assertEqual(assassin_view.visible_roles[assassin.id], Role.ASSASSIN)
        self.assertEqual(minion_view.visible_roles[minion.id], Role.MINION)
        self.assertEqual(assassin_view.known_evil_player_ids, [minion.id])
        self.assertEqual(minion_view.known_evil_player_ids, [assassin.id])
        self.assertEqual(assassin_view.visible_roles[minion.id], Role.UNKNOWN_EVIL)
        self.assertEqual(minion_view.visible_roles[assassin.id], Role.UNKNOWN_EVIL)

    def test_loyal_servant_private_view_has_no_hidden_identity_information(self):
        state = create_five_player_game(seed=19)
        loyal = player_with_role(state, Role.LOYAL_SERVANT)
        other_players = [player for player in state.players if player.id != loyal.id]

        view = private_view_for_player(state, loyal.id)

        self.assertEqual(view.visible_roles[loyal.id], Role.LOYAL_SERVANT)
        self.assertEqual(view.known_evil_player_ids, [])
        self.assertTrue(all(view.visible_roles[player.id] is None for player in other_players))

