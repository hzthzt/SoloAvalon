import unittest

from backend.app.game.models import Faction, GameOption, Phase, Role
from backend.app.game.rules import (
    STANDARD_FACTION_COUNTS,
    STANDARD_MISSION_CONFIGS,
    create_game,
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
                Role.PERCIVAL,
                Role.MORGANA,
                Role.LOYAL_SERVANT,
            ],
        )
        self.assertEqual(state.players[0].id, "player_1")
        self.assertTrue(state.players[0].is_human)
        self.assertEqual(state.players[0].name, "玩家1")
        self.assertEqual(state.players[0].original_name, "真人玩家")
        self.assertFalse(state.players[1].is_human)
        self.assertEqual(state.phase, Phase.TEAM_PROPOSAL)
        self.assertEqual(state.current_round, 1)
        self.assertEqual(state.leader_index, 0)
        self.assertEqual(
            [(mission.team_size, mission.fail_cards_required) for mission in state.missions],
            [(2, 1), (3, 1), (2, 1), (3, 1), (3, 1)],
        )

    def test_create_game_supports_standard_five_to_ten_player_setups(self):
        expected_roles = {
            5: [
                Role.MERLIN,
                Role.PERCIVAL,
                Role.LOYAL_SERVANT,
                Role.ASSASSIN,
                Role.MORGANA,
            ],
            6: [
                Role.MERLIN,
                Role.PERCIVAL,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.ASSASSIN,
                Role.MORGANA,
            ],
            7: [
                Role.MERLIN,
                Role.PERCIVAL,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.ASSASSIN,
                Role.MORGANA,
                Role.OBERON,
            ],
            8: [
                Role.MERLIN,
                Role.PERCIVAL,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.ASSASSIN,
                Role.MORGANA,
                Role.MINION,
            ],
            9: [
                Role.MERLIN,
                Role.PERCIVAL,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.LOYAL_SERVANT,
                Role.ASSASSIN,
                Role.MORGANA,
                Role.MORDRED,
            ],
            10: [
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
            ],
        }
        for player_count in range(5, 11):
            with self.subTest(player_count=player_count):
                state = create_game(
                    player_count=player_count,
                    seed=player_count,
                    human_seat_index=0,
                )

                self.assertEqual(len(state.players), player_count)
                self.assertEqual(state.players[0].id, "player_1")
                self.assertTrue(state.players[0].is_human)
                self.assertCountEqual(
                    [player.role for player in state.players],
                    expected_roles[player_count],
                )
                good_count, evil_count = STANDARD_FACTION_COUNTS[player_count]
                self.assertEqual(
                    sum(1 for player in state.players if player.faction == Faction.GOOD),
                    good_count,
                )
                self.assertEqual(
                    sum(1 for player in state.players if player.faction == Faction.EVIL),
                    evil_count,
                )
                self.assertEqual(
                    state.missions,
                    STANDARD_MISSION_CONFIGS[player_count],
                )

    def test_create_game_rejects_invalid_player_count(self):
        for player_count in [4, 11]:
            with self.subTest(player_count=player_count):
                with self.assertRaises(ValueError):
                    create_game(player_count=player_count)

    def test_lady_of_lake_option_is_only_available_for_eight_to_ten_players(self):
        for player_count in range(5, 8):
            with self.subTest(player_count=player_count):
                with self.assertRaises(ValueError):
                    create_game(
                        player_count=player_count,
                        enabled_options={GameOption.LADY_OF_LAKE},
                    )
        for player_count in range(8, 11):
            with self.subTest(player_count=player_count):
                state = create_game(
                    player_count=player_count,
                    seed=player_count,
                    enabled_options={GameOption.LADY_OF_LAKE},
                )

                self.assertIn(GameOption.LADY_OF_LAKE, state.enabled_options)
                self.assertEqual(
                    state.lady_of_lake_holder_player_id,
                    f"player_{player_count}",
                )
                self.assertEqual(
                    state.lady_of_lake_previous_holder_ids,
                    (f"player_{player_count}",),
                )

    def test_tristan_isolde_option_is_only_available_for_nine_to_ten_players(self):
        for player_count in range(5, 9):
            with self.subTest(player_count=player_count):
                with self.assertRaises(ValueError):
                    create_game(
                        player_count=player_count,
                        enabled_options={GameOption.TRISTAN_ISOLDE},
                    )
        for player_count in range(9, 11):
            with self.subTest(player_count=player_count):
                state = create_game(
                    player_count=player_count,
                    seed=player_count,
                    enabled_options={GameOption.TRISTAN_ISOLDE},
                )

                self.assertIn(GameOption.TRISTAN_ISOLDE, state.enabled_options)
                self.assertCountEqual(
                    [player.role for player in state.players if player.faction == Faction.GOOD],
                    [
                        Role.MERLIN,
                        Role.PERCIVAL,
                        Role.TRISTAN,
                        Role.ISOLDE,
                        *[Role.LOYAL_SERVANT]
                        * (STANDARD_FACTION_COUNTS[player_count][0] - 4),
                    ],
                )

    def test_create_five_player_game_randomizes_human_seat_and_keeps_original_names(self):
        state = create_five_player_game(
            seed=20260619,
            human_name="张三",
            ai_names=["阿尔法", "贝塔", "伽马", "德尔塔"],
        )

        self.assertEqual(
            [player.name for player in state.players],
            ["玩家1", "玩家2", "玩家3", "玩家4", "玩家5"],
        )
        human = next(player for player in state.players if player.is_human)
        self.assertNotEqual(human.id, "player_1")
        self.assertEqual(human.original_name, "张三")
        self.assertCountEqual(
            [player.original_name for player in state.players if not player.is_human],
            ["阿尔法", "贝塔", "伽马", "德尔塔"],
        )

    def test_factions_derive_from_roles(self):
        state = create_five_player_game(seed=3)

        self.assertEqual(player_with_role(state, Role.MERLIN).faction, Faction.GOOD)
        self.assertEqual(player_with_role(state, Role.LOYAL_SERVANT).faction, Faction.GOOD)
        self.assertEqual(player_with_role(state, Role.PERCIVAL).faction, Faction.GOOD)
        self.assertEqual(player_with_role(state, Role.ASSASSIN).faction, Faction.EVIL)
        self.assertEqual(player_with_role(state, Role.MORGANA).faction, Faction.EVIL)

    def test_merlin_private_view_knows_evil_players(self):
        state = create_five_player_game(seed=11)
        merlin = player_with_role(state, Role.MERLIN)
        assassin = player_with_role(state, Role.ASSASSIN)
        morgana = player_with_role(state, Role.MORGANA)

        view = private_view_for_player(state, merlin.id)

        self.assertEqual(view.viewer_player_id, merlin.id)
        self.assertCountEqual(view.known_evil_player_ids, [assassin.id, morgana.id])
        self.assertEqual(view.visible_roles[merlin.id], Role.MERLIN)
        self.assertEqual(view.visible_roles[assassin.id], Role.UNKNOWN_EVIL)
        self.assertEqual(view.visible_roles[morgana.id], Role.UNKNOWN_EVIL)

    def test_evil_players_know_each_other_but_not_exact_roles(self):
        state = create_five_player_game(seed=13)
        assassin = player_with_role(state, Role.ASSASSIN)
        morgana = player_with_role(state, Role.MORGANA)

        assassin_view = private_view_for_player(state, assassin.id)
        morgana_view = private_view_for_player(state, morgana.id)

        self.assertEqual(assassin_view.visible_roles[assassin.id], Role.ASSASSIN)
        self.assertEqual(morgana_view.visible_roles[morgana.id], Role.MORGANA)
        self.assertEqual(assassin_view.known_evil_player_ids, [morgana.id])
        self.assertEqual(morgana_view.known_evil_player_ids, [assassin.id])
        self.assertEqual(assassin_view.visible_roles[morgana.id], Role.UNKNOWN_EVIL)
        self.assertEqual(morgana_view.visible_roles[assassin.id], Role.UNKNOWN_EVIL)

    def test_extended_identity_visibility_rules(self):
        from backend.app.game.models import GameState, MissionConfig, Player

        state = GameState(
            players=(
                Player("player_1", 0, "Merlin", False, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "Percival", False, Role.PERCIVAL, Faction.GOOD),
                Player("player_3", 2, "Tristan", False, Role.TRISTAN, Faction.GOOD),
                Player("player_4", 3, "Isolde", False, Role.ISOLDE, Faction.GOOD),
                Player("player_5", 4, "Assassin", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_6", 5, "Morgana", False, Role.MORGANA, Faction.EVIL),
                Player("player_7", 6, "Mordred", False, Role.MORDRED, Faction.EVIL),
                Player("player_8", 7, "Oberon", False, Role.OBERON, Faction.EVIL),
            ),
            missions=(MissionConfig(round_number=1, team_size=3, fail_cards_required=1),),
        )

        merlin_view = private_view_for_player(state, "player_1")
        percival_view = private_view_for_player(state, "player_2")
        assassin_view = private_view_for_player(state, "player_5")
        oberon_view = private_view_for_player(state, "player_8")
        tristan_view = private_view_for_player(state, "player_3")

        self.assertCountEqual(merlin_view.known_evil_player_ids, ["player_5", "player_6", "player_8"])
        self.assertIsNone(merlin_view.visible_roles["player_7"])
        self.assertCountEqual(percival_view.merlin_candidate_player_ids, ["player_1", "player_6"])
        self.assertEqual(percival_view.visible_roles["player_1"], Role.UNKNOWN_MERLIN)
        self.assertEqual(percival_view.visible_roles["player_6"], Role.UNKNOWN_MERLIN)
        self.assertCountEqual(assassin_view.known_evil_player_ids, ["player_6", "player_7"])
        self.assertNotIn("player_8", assassin_view.known_evil_player_ids)
        self.assertEqual(oberon_view.known_evil_player_ids, [])
        self.assertEqual(tristan_view.known_good_player_ids, ["player_4"])
        self.assertEqual(tristan_view.visible_roles["player_4"], Role.ISOLDE)

    def test_loyal_servant_private_view_has_no_hidden_identity_information(self):
        state = create_five_player_game(seed=19)
        loyal = player_with_role(state, Role.LOYAL_SERVANT)
        other_players = [player for player in state.players if player.id != loyal.id]

        view = private_view_for_player(state, loyal.id)

        self.assertEqual(view.visible_roles[loyal.id], Role.LOYAL_SERVANT)
        self.assertEqual(view.known_evil_player_ids, [])
        self.assertTrue(all(view.visible_roles[player.id] is None for player in other_players))

