import tempfile
import unittest
from pathlib import Path

from backend.app.game.rules import create_five_player_game
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.event_store import EventStore
from backend.app.storage.game_repository import GameRepository


class EventStoreTests(unittest.TestCase):
    def test_append_event_assigns_sequential_indexes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=10),
                    game_id="game_1",
                )
                store = EventStore(connection)

                first = store.append_event(
                    "game_1",
                    "game_created",
                    public_payload={"game_id": "game_1"},
                    private_payload={"seed": 10},
                )
                second = store.append_event(
                    "game_1",
                    "leader_changed",
                    public_payload={"leader_player_id": "player_1"},
                )

                self.assertEqual(first.event_index, 1)
                self.assertEqual(second.event_index, 2)
                self.assertEqual(
                    [event.event_type for event in store.list_events("game_1")],
                    [
                        "game_created",
                        "leader_changed",
                    ],
                )
            finally:
                connection.close()

    def test_export_game_log_excludes_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=11),
                    game_id="game_1",
                )
                store = EventStore(connection)
                store.append_event(
                    "game_1",
                    "roles_assigned",
                    public_payload={"player_count": 5},
                    private_payload={"roles": {"player_1": "merlin"}},
                )

                public_export = store.export_game_log("game_1")
                private_export = store.export_game_log("game_1", include_private=True)

                self.assertNotIn("private_payload", public_export["events"][0])
                self.assertEqual(
                    private_export["events"][0]["private_payload"],
                    {"roles": {"player_1": "merlin"}},
                )
            finally:
                connection.close()

    def test_list_events_after_returns_only_newer_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=12),
                    game_id="game_1",
                )
                store = EventStore(connection)
                store.append_event("game_1", "game_created", public_payload={"game_id": "game_1"})
                second = store.append_event(
                    "game_1",
                    "leader_changed",
                    public_payload={"leader_player_id": "player_1"},
                )
                newer_events = store.list_events_after("game_1", 1)

                self.assertEqual([event.event_index for event in newer_events], [second.event_index])
                self.assertEqual(newer_events[0].event_type, "leader_changed")
            finally:
                connection.close()

