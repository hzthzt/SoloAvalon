import tempfile
import unittest
from pathlib import Path

from backend.app.game.models import Phase
from backend.app.game.rules import create_five_player_game
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.game_repository import GameRepository


class GameRepositoryTests(unittest.TestCase):
    def test_save_new_game_persists_summary_and_players(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)
                state = create_five_player_game(seed=20260615)

                repository.save_new_game(state, game_id="game_1")
                summary = repository.get_game_summary("game_1")
                players = repository.list_players("game_1")

                self.assertEqual(summary.id, "game_1")
                self.assertEqual(summary.status, "active")
                self.assertEqual(summary.player_count, 5)
                self.assertEqual(summary.current_round, 1)
                self.assertEqual(summary.current_phase, Phase.TEAM_PROPOSAL.value)
                self.assertIsNone(summary.winner)
                self.assertEqual(len(players), 5)
                self.assertEqual(players[0].game_id, "game_1")
                self.assertEqual(players[0].seat_index, 0)
                self.assertEqual(players[0].name, "You")
            finally:
                connection.close()

    def test_list_games_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)

                repository.save_new_game(create_five_player_game(seed=1), game_id="game_old")
                repository.save_new_game(create_five_player_game(seed=2), game_id="game_new")

                summaries = repository.list_games()

                self.assertEqual([summary.id for summary in summaries], ["game_new", "game_old"])
            finally:
                connection.close()

    def test_delete_game_removes_players_by_cascade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)

                repository.save_new_game(create_five_player_game(seed=3), game_id="game_1")
                repository.delete_game("game_1")

                self.assertIsNone(repository.get_game_summary("game_1"))
                self.assertEqual(repository.list_players("game_1"), [])
            finally:
                connection.close()

