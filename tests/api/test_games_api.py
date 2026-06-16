import tempfile
import unittest
from pathlib import Path

from backend.app.api.games import GamesApi
from backend.app.services.game_service import GameService
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.event_store import EventStore


class GamesApiTests(unittest.TestCase):
    def test_games_api_does_not_forward_seed_to_service(self):
        class RecordingService:
            def __init__(self):
                self.create_kwargs = None

            def create_game(self, **kwargs):
                self.create_kwargs = kwargs
                return {"id": "game_1"}

        service = RecordingService()
        api = GamesApi(service)

        api.create_game({"seed": 9, "ai_names": ["A"]})

        self.assertEqual(service.create_kwargs, {
            "ai_names": ["A"],
            "default_llm_profile_id": None,
            "ai_profile_overrides": None,
        })

    def test_games_api_creates_and_returns_game_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)

            created = api.create_game({})
            loaded = api.get_game(created["id"])

            self.assertEqual(loaded["id"], created["id"])
            self.assertEqual(loaded["human_player_id"], "player_1")

    def test_games_api_submits_action_and_exports_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api.create_game({})

            updated = api.submit_action(
                created["id"],
                {"action_type": "propose_team", "team": ["player_1", "player_2"]},
            )
            exported = api.export_game(created["id"])

            self.assertEqual(updated["next_human_action"], "speak")
            self.assertEqual(exported["game"]["id"], created["id"])

    def test_games_api_allows_ai_to_drive_current_human_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api.create_game({})

            updated = api.submit_human_ai_action(created["id"])

            self.assertEqual(updated["phase"], "speech")
            self.assertEqual(updated["next_human_action"], "speak")

    def test_list_events_hides_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api.create_game({})

            public_events = api.list_events(created["id"])

            self.assertNotIn("private_payload", public_events[1])

    def test_list_events_can_include_private_payload_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api.create_game({})

            private_events = api.list_events(created["id"], include_private=True)

            self.assertIn("private_payload", private_events[1])
            self.assertIn("roles_by_player_id", private_events[1]["private_payload"])

    def test_public_events_hide_vote_values_until_vote_result_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(":memory:")
            initialize_database(connection)
            service = GameService(connection)
            api = GamesApi(service)
            event_store = EventStore(connection)
            state = api.create_game({})
            event_store.append_event(
                state["id"],
                "vote_cast",
                {"player_id": "player_2", "vote": "approve"},
            )

            in_progress_events = api.list_events(state["id"])
            in_progress_export = api.export_game(state["id"])
            hidden_vote_events = [
                event for event in in_progress_events if event["event_type"] == "vote_cast"
            ]
            hidden_export_votes = [
                event for event in in_progress_export["events"] if event["event_type"] == "vote_cast"
            ]

            self.assertGreater(len(hidden_vote_events), 0)
            self.assertTrue(
                all("vote" not in event["public_payload"] for event in hidden_vote_events)
            )
            self.assertTrue(
                all("vote" not in event["public_payload"] for event in hidden_export_votes)
            )

            event_store.append_event(
                state["id"],
                "vote_result",
                {"approved": True, "failed_team_votes": 0},
            )
            settled_events = api.list_events(state["id"])
            settled_vote_events = [
                event for event in settled_events if event["event_type"] == "vote_cast"
            ]

            self.assertTrue(
                all("vote" in event["public_payload"] for event in settled_vote_events)
            )


def _api(tmpdir):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    return GamesApi(GameService(connection))
