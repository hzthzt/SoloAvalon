import unittest

from backend.app.ai.context import ContextBuilder
from backend.app.game.models import Phase, Role
from backend.app.game.rules import create_five_player_game, propose_team


class ContextBuilderTests(unittest.TestCase):
    def test_loyal_servant_context_does_not_reveal_hidden_roles(self):
        state = create_five_player_game(seed=20260615)
        loyal = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)

        context = ContextBuilder().build(state, loyal.id, Phase.SPEECH)

        self.assertEqual(context.private_view["viewer_role"], Role.LOYAL_SERVANT.value)
        other_visible_roles = {
            player_id: role
            for player_id, role in context.private_view["visible_roles"].items()
            if player_id != loyal.id
        }
        self.assertEqual(set(other_visible_roles.values()), {None})
        self.assertNotIn("merlin", context.dynamic_private_suffix.lower())
        self.assertNotIn("assassin", context.dynamic_private_suffix.lower())
        self.assertNotIn("minion", context.dynamic_private_suffix.lower())

    def test_merlin_context_can_see_unknown_evil_without_exact_evil_roles(self):
        state = create_five_player_game(seed=20260615)
        merlin = next(player for player in state.players if player.role == Role.MERLIN)

        context = ContextBuilder().build(state, merlin.id, Phase.SPEECH)

        self.assertEqual(len(context.private_view["known_evil_player_ids"]), 2)
        visible_roles = context.private_view["visible_roles"]
        for evil_id in context.private_view["known_evil_player_ids"]:
            self.assertEqual(visible_roles[evil_id], Role.UNKNOWN_EVIL.value)
        self.assertNotIn("assassin", context.dynamic_private_suffix.lower())
        self.assertNotIn("minion", context.dynamic_private_suffix.lower())

    def test_stable_prefix_hash_is_independent_of_dynamic_game_state(self):
        builder = ContextBuilder()
        first = create_five_player_game(seed=1)
        second = propose_team(first, first.players[0].id, ("player_1", "player_2"))

        first_context = builder.build(first, first.players[1].id, Phase.TEAM_PROPOSAL)
        second_context = builder.build(second, second.players[1].id, Phase.SPEECH)

        self.assertEqual(first_context.stable_prefix_hash, second_context.stable_prefix_hash)
        self.assertNotEqual(first_context.dynamic_private_suffix, second_context.dynamic_private_suffix)

    def test_context_marks_truncation_when_recent_event_budget_is_exceeded(self):
        state = create_five_player_game(seed=5)
        events = [
            {"event_type": "speech", "public_payload": {"message": f"message {index}"}}
            for index in range(10)
        ]

        context = ContextBuilder(max_recent_events=3).build(
            state,
            state.players[1].id,
            Phase.SPEECH,
            public_events=events,
        )

        self.assertTrue(context.context_truncated)
        self.assertIn("message 9", context.dynamic_private_suffix)
        self.assertNotIn("message 0", context.dynamic_private_suffix)
