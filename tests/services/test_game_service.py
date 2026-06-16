import tempfile
import unittest
from pathlib import Path

import backend.app.services.game_service as game_service_module
from backend.app.ai.player import AiTurnResult
from backend.app.ai.strategy import SpeechDecision
from backend.app.game.models import Phase, Role
from backend.app.services.game_service import GameService
from backend.app.storage.ai_decision_repository import AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository
from backend.app.storage.database import connect_sqlite, initialize_database


class GameServiceTests(unittest.TestCase):
    def test_create_game_returns_human_filtered_state_and_logs_setup_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(seed=20260615)

            self.assertEqual(state["phase"], Phase.TEAM_PROPOSAL.value)
            self.assertEqual(state["human_player_id"], "player_1")
            visible_roles = {
                player["id"]: player["visible_role"]
                for player in state["players"]
            }
            self.assertEqual(visible_roles["player_1"], state["human_role"])
            hidden_roles = [
                role for player_id, role in visible_roles.items() if player_id != "player_1"
            ]
            if state["human_role"] == Role.MERLIN.value:
                self.assertEqual(set(hidden_roles), {None, Role.UNKNOWN_EVIL.value})
            else:
                self.assertNotIn(Role.MERLIN.value, hidden_roles)

            events = service.list_events(state["id"])
            self.assertEqual(events[0].event_type, "game_created")
            self.assertEqual(events[1].event_type, "roles_assigned")

    def test_human_actions_trigger_ai_until_next_human_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            self.assertEqual(state["phase"], Phase.SPEECH.value)
            self.assertEqual(state["next_human_action"], "speak")

            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )
            self.assertEqual(state["phase"], Phase.VOTING.value)
            self.assertEqual(state["next_human_action"], "vote")
            self.assertEqual(len(state["speeches"]), 5)

            state = service.submit_human_action(
                state["id"],
                "vote",
                {"vote": "approve"},
            )

            self.assertEqual(state["phase"], Phase.QUEST.value)
            self.assertEqual(state["next_human_action"], "mission_action")

    def test_ai_can_submit_current_human_decision_for_testing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            updated = service.submit_human_ai_action(state["id"])
            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])

            self.assertEqual(updated["phase"], Phase.SPEECH.value)
            self.assertEqual(updated["next_human_action"], "speak")
            self.assertIn("team_proposal", {decision.decision_type for decision in decisions})
            self.assertIn("player_1", {decision.player_id for decision in decisions})

    def test_ai_controlled_state_includes_public_event_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            updated = service.submit_human_ai_action(state["id"])

            event_types = {event["event_type"] for event in updated["events"]}
            self.assertIn("ai_decision", event_types)
            self.assertIn("team_proposed", event_types)
            self.assertTrue(
                all("private_payload" not in event for event in updated["events"])
            )

    def test_ai_turns_are_persisted_as_ai_decisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])

            self.assertGreaterEqual(len(decisions), 4)
            self.assertIn("speech", {decision.decision_type for decision in decisions})
            self.assertTrue(all(decision.prompt_template_version for decision in decisions))

    def test_ai_turns_are_persisted_as_private_memory_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            snapshots = AiMemoryRepository(service.connection).list_snapshots(state["id"])

            self.assertGreaterEqual(len(snapshots), 4)
            self.assertTrue(all("suspicions" in snapshot.memory_payload for snapshot in snapshots))
            self.assertTrue(all("key_observations" in snapshot.memory_payload for snapshot in snapshots))

    def test_ai_turns_receive_public_event_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class RecordingAiPlayer:
                def __init__(self):
                    self.received_events = []

                def speak(self, state, player_id, profile, public_events=None):
                    self.received_events.append(public_events)
                    return AiTurnResult(
                        decision=SpeechDecision(
                            public_message=f"{player_id} has noted the history.",
                            stance="uncertain",
                            private_reason_summary="recorded public history",
                        ),
                        input_summary="recorded",
                        strategy_summary="recorded public history",
                        output_raw=None,
                        output_parsed=None,
                        validation_status="fallback",
                        prompt_template_name="speech",
                        prompt_template_version="prompt.v1",
                        context_builder_version="context-builder.v1",
                        stable_prefix_hash="hash",
                        context_summary="recorded",
                        context_truncated=False,
                    )

            ai_player = RecordingAiPlayer()
            service = _service(tmpdir, ai_player=ai_player)
            state = service.create_game(seed=1)
            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            self.assertGreater(len(ai_player.received_events), 0)
            first_history = ai_player.received_events[0]
            self.assertIsNotNone(first_history)
            self.assertIn("team_proposed", {event["event_type"] for event in first_history})
            self.assertTrue(all("private_payload" not in event for event in first_history))

    def test_default_ai_player_uses_live_provider_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(":memory:")
            initialize_database(connection)
            providers = []

            class RecordingAiPlayer:
                def __init__(self, provider=None):
                    providers.append(provider)

            original_ai_player = game_service_module.AiPlayer
            try:
                game_service_module.AiPlayer = RecordingAiPlayer
                GameService(connection)
            finally:
                game_service_module.AiPlayer = original_ai_player
                connection.close()

            self.assertEqual(providers, [None])

    def test_export_game_log_excludes_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=6)

            public_export = service.export_game_log(state["id"])
            private_export = service.export_game_log(state["id"], include_private=True)

            self.assertNotIn("private_payload", public_export["events"][1])
            self.assertIn("private_payload", private_export["events"][1])

    def test_delete_game_removes_log_from_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=7)

            self.assertEqual(len(service.list_games()), 1)
            service.delete_game(state["id"])

            self.assertEqual(service.list_games(), [])
            with self.assertRaises(ValueError):
                service.get_game_state(state["id"])

    def test_active_game_state_can_be_restored_from_persisted_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(database_path)
            try:
                initialize_database(connection)
                service = GameService(connection)
                state = service.create_game(seed=1)
                state = service.submit_human_action(
                    state["id"],
                    "propose_team",
                    {"team": ["player_1", "player_2"]},
                )
                state = service.submit_human_action(
                    state["id"],
                    "speak",
                    {"message": "I trust this opening team."},
                )
                state = service.submit_human_action(
                    state["id"],
                    "vote",
                    {"vote": "approve"},
                )
            finally:
                connection.close()

            restored_connection = connect_sqlite(database_path)
            try:
                initialize_database(restored_connection)
                restored_service = GameService(restored_connection)

                restored = restored_service.get_game_state(state["id"])

                self.assertEqual(restored["phase"], state["phase"])
                self.assertEqual(restored["next_human_action"], "mission_action")
                self.assertEqual(restored["speeches"], state["speeches"])
                self.assertEqual(
                    restored["quest_actions_submitted_count"],
                    state["quest_actions_submitted_count"],
                )
            finally:
                restored_connection.close()


def _service(tmpdir, ai_player=None):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    return GameService(connection, ai_player=ai_player)
