import tempfile
import unittest
from pathlib import Path

from backend.app.game.models import GameOption, Phase
from backend.app.game.rules import create_five_player_game, create_game, propose_team
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

                repository.save_new_game(state, game_id="game_1", display_name="游戏#1")
                summary = repository.get_game_summary("game_1")
                players = repository.list_players("game_1")

                self.assertEqual(summary.id, "game_1")
                self.assertEqual(summary.display_name, "游戏#1")
                self.assertEqual(summary.status, "active")
                self.assertEqual(summary.player_count, 5)
                self.assertEqual(summary.enabled_options, [])
                self.assertEqual(summary.current_round, 1)
                self.assertEqual(summary.current_phase, Phase.TEAM_PROPOSAL.value)
                self.assertIsNone(summary.winner)
                self.assertIsNone(summary.archived_at)
                self.assertEqual(len(players), 5)
                self.assertEqual(players[0].game_id, "game_1")
                self.assertEqual(players[0].seat_index, 0)
                self.assertEqual(players[0].name, "玩家1")
                self.assertIsNotNone(players[0].original_name)
            finally:
                connection.close()

    def test_save_new_game_persists_enabled_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)
                state = create_game(
                    player_count=8,
                    seed=8,
                    enabled_options={GameOption.LADY_OF_LAKE, GameOption.ROLE_TIP_DETAIL},
                )

                repository.save_new_game(state, game_id="game_options")
                summary = repository.get_game_summary("game_options")

                self.assertEqual(
                    summary.enabled_options,
                    ["lady_of_lake", "role_tip_detail"],
                )
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

    def test_next_room_display_name_uses_display_sequence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)
                state = create_five_player_game(seed=1)

                repository.save_new_game(state, game_id="20260620_120000_000001")
                repository.save_new_game(state, game_id="legacy_hash_id", display_name="游戏#1")
                repository.save_new_game(state, game_id="another_legacy_id", display_name="游戏#7")

                self.assertEqual(repository.next_game_id(), "game_1")
                self.assertEqual(repository.next_room_display_name(), "游戏#8")
            finally:
                connection.close()

    def test_archive_game_sets_archived_at_idempotently(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = GameRepository(connection)
                repository.save_new_game(create_five_player_game(seed=4), game_id="game_1")

                first = repository.archive_game("game_1")
                second = repository.archive_game("game_1")
                summary = repository.get_game_summary("game_1")

                self.assertIsNotNone(first.archived_at)
                self.assertEqual(second.archived_at, first.archived_at)
                self.assertEqual(summary.archived_at, first.archived_at)
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

    def test_update_game_state_can_defer_commit_to_outer_transaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                state = create_five_player_game(seed=5)
                repository = GameRepository(connection)
                repository.save_new_game(state, game_id="game_1")
                transactional_repository = GameRepository(connection, autocommit=False)
                next_state = propose_team(state, "player_1", ("player_1", "player_2"))

                transactional_repository.update_game_state("game_1", next_state)
                connection.rollback()

                summary = repository.get_game_summary("game_1")
                self.assertEqual(summary.current_phase, Phase.TEAM_PROPOSAL.value)
            finally:
                connection.close()

