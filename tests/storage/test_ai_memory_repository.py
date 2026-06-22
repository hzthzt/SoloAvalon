import tempfile
import unittest
from pathlib import Path

from backend.app.game.models import Phase
from backend.app.game.rules import create_five_player_game
from backend.app.storage.ai_memory_repository import (
    AiMemoryRepository,
    AiMemorySnapshotInput,
)
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.game_repository import GameRepository


class AiMemoryRepositoryTests(unittest.TestCase):
    def test_save_and_list_memory_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=20260615),
                    game_id="game_1",
                )
                repository = AiMemoryRepository(connection)

                saved = repository.save_snapshot(
                    AiMemorySnapshotInput(
                        game_id="game_1",
                        player_id="player_2",
                        round_number=1,
                        phase=Phase.SPEECH.value,
                        memory_payload={
                            "suspicions": {"player_1": 0.25},
                            "trusted_players": ["player_2"],
                            "key_observations": ["player_2 spoke cautiously"],
                            "merlin_candidates": [],
                        },
                    )
                )

                snapshots = repository.list_snapshots("game_1", "player_2")

                self.assertEqual(saved.id, 1)
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0].round_number, 1)
                self.assertEqual(snapshots[0].memory_payload["suspicions"]["player_1"], 0.25)
            finally:
                connection.close()

    def test_save_snapshot_can_defer_commit_to_outer_transaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=20260616),
                    game_id="game_1",
                )
                repository = AiMemoryRepository(connection, autocommit=False)

                repository.save_snapshot(
                    AiMemorySnapshotInput(
                        game_id="game_1",
                        player_id="player_2",
                        round_number=1,
                        phase=Phase.SPEECH.value,
                        memory_payload={
                            "suspicions": {"player_1": 0.25},
                            "trusted_players": ["player_2"],
                            "key_observations": ["player_2 spoke cautiously"],
                            "merlin_candidates": [],
                        },
                    )
                )
                connection.rollback()

                self.assertEqual(repository.list_snapshots("game_1", "player_2"), [])
            finally:
                connection.close()
