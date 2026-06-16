import unittest

from backend.app.game.events import (
    build_game_created_payloads,
    build_private_view_payloads,
    build_roles_assigned_payloads,
)
from backend.app.game.models import Role
from backend.app.game.rules import create_five_player_game


class EventPayloadTests(unittest.TestCase):
    def test_game_created_payload_has_public_game_shape(self):
        state = create_five_player_game(seed=30)

        public_payload, private_payload = build_game_created_payloads(state, game_id="game_1")

        self.assertEqual(public_payload["game_id"], "game_1")
        self.assertEqual(public_payload["player_count"], 5)
        self.assertEqual(public_payload["current_round"], 1)
        self.assertEqual(public_payload["current_phase"], "team_proposal")
        self.assertIsNone(private_payload)

    def test_roles_assigned_payload_keeps_truth_private(self):
        state = create_five_player_game(seed=31)

        public_payload, private_payload = build_roles_assigned_payloads(state)

        self.assertEqual(public_payload, {"player_count": 5})
        self.assertEqual(
            set(private_payload["roles_by_player_id"]),
            {
                "player_1",
                "player_2",
                "player_3",
                "player_4",
                "player_5",
            },
        )

    def test_private_view_payload_contains_only_legal_view(self):
        state = create_five_player_game(seed=32)
        loyal = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)

        public_payload, private_payload = build_private_view_payloads(state, loyal.id)

        self.assertEqual(public_payload["viewer_player_id"], loyal.id)
        self.assertEqual(private_payload["visible_roles"][loyal.id], Role.LOYAL_SERVANT.value)
        hidden_other_roles = [
            role
            for player_id, role in private_payload["visible_roles"].items()
            if player_id != loyal.id
        ]
        self.assertEqual(hidden_other_roles, [None, None, None, None])

