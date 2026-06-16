import tempfile
import unittest
from pathlib import Path

from backend.app.llm.profiles import LlmProfileInput
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.game_repository import GameRepository
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from backend.app.game.rules import create_five_player_game


def profile_input(
    name="DeepSeek",
    api_key="test-key-1234567890abcdef",
    model="deepseek-chat",
) -> LlmProfileInput:
    return LlmProfileInput(
        name=name,
        base_url="https://api.example.com/v1",
        api_key=api_key,
        model=model,
        temperature=0.7,
        timeout=30.0,
    )


class LlmProfileRepositoryTests(unittest.TestCase):
    def test_missing_config_file_lists_no_profiles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)

                self.assertEqual(repository.list_profiles(), [])
                self.assertIsNone(repository.get_profile("missing"))
            finally:
                connection.close()

    def test_create_list_and_get_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _config_path(tmpdir)
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)

                created = repository.create_profile("profile_1", profile_input())
                listed = repository.list_profiles()
                loaded = repository.get_profile("profile_1")

                self.assertEqual(created.id, "profile_1")
                self.assertEqual(created.api_key, "test-key-1234567890abcdef")
                self.assertEqual([profile.id for profile in listed], ["profile_1"])
                self.assertEqual(loaded.model, "deepseek-chat")
                self.assertTrue(config_path.exists())
                self.assertIn("test-key-1234567890abcdef", config_path.read_text(encoding="utf-8"))
                self.assertNotIn("test-key-1234567890abcdef", _sqlite_text(connection))
            finally:
                connection.close()

    def test_public_profile_dict_never_exposes_api_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)

                profile = repository.create_profile("profile_1", profile_input())

                public_dict = profile.to_public_dict()
                self.assertEqual(public_dict["api_key_masked"], "test...cdef")
                self.assertNotIn("api_key", public_dict)
            finally:
                connection.close()

    def test_update_profile_replaces_config_and_preserves_created_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)
                created = repository.create_profile("profile_1", profile_input())

                updated = repository.update_profile(
                    "profile_1",
                    profile_input(
                        name="Qwen",
                        api_key="test-key-updated12345678",
                        model="qwen-plus",
                    ),
                )

                self.assertEqual(updated.name, "Qwen")
                self.assertEqual(updated.model, "qwen-plus")
                self.assertEqual(updated.api_key, "test-key-updated12345678")
                self.assertEqual(updated.created_at, created.created_at)
                self.assertGreaterEqual(updated.updated_at, created.updated_at)
            finally:
                connection.close()

    def test_delete_profile_removes_it_from_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)
                repository.create_profile("profile_1", profile_input())

                repository.delete_profile("profile_1")

                self.assertIsNone(repository.get_profile("profile_1"))
                self.assertEqual(repository.list_profiles(), [])
            finally:
                connection.close()

    def test_invalid_config_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = _config_path(tmpdir)
            config_path.parent.mkdir(parents=True)
            config_path.write_text("{not valid json", encoding="utf-8")
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                repository = _repository(connection, tmpdir)

                with self.assertRaises(ValueError):
                    repository.list_profiles()
            finally:
                connection.close()

    def test_resolve_profile_uses_player_override_before_game_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                profiles = _repository(connection, tmpdir)
                games = GameRepository(connection)
                profiles.create_profile("default_profile", profile_input(name="Default"))
                profiles.create_profile(
                    "override_profile",
                    profile_input(name="Override", api_key="test-key-override123456", model="qwen-plus"),
                )
                games.save_new_game(
                    create_five_player_game(seed=41),
                    game_id="game_1",
                    default_llm_profile_id="default_profile",
                )
                games.set_player_llm_profile(
                    "game_1",
                    "player_3",
                    "override_profile",
                )

                default_resolved = profiles.resolve_profile_for_player("game_1", "player_2")
                override_resolved = profiles.resolve_profile_for_player("game_1", "player_3")

                self.assertEqual(default_resolved.id, "default_profile")
                self.assertEqual(override_resolved.id, "override_profile")
            finally:
                connection.close()

    def test_resolve_profile_rejects_game_without_default_or_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                profiles = _repository(connection, tmpdir)
                games = GameRepository(connection)
                games.save_new_game(create_five_player_game(seed=42), game_id="game_1")

                with self.assertRaises(ValueError):
                    profiles.resolve_profile_for_player("game_1", "player_2")
            finally:
                connection.close()

    def test_setting_missing_override_profile_stores_id_without_sqlite_profile_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                games = GameRepository(connection)
                games.save_new_game(
                    create_five_player_game(seed=43),
                    game_id="game_1",
                )

                games.set_player_llm_profile("game_1", "player_2", "missing_profile")

                stored_player = next(
                    player for player in games.list_players("game_1") if player.id == "player_2"
                )
                self.assertEqual(stored_player.llm_profile_id, "missing_profile")
            finally:
                connection.close()


def _config_path(tmpdir: str) -> Path:
    return Path(tmpdir) / "config" / "llm_profiles.json"


def _repository(connection, tmpdir: str) -> LlmProfileRepository:
    return LlmProfileRepository(connection, config_path=_config_path(tmpdir))


def _sqlite_text(connection) -> str:
    table_names = [
        row["name"]
        for row in connection.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
        if not row["name"].startswith("sqlite_")
    ]
    chunks: list[str] = []
    for table_name in table_names:
        rows = connection.execute(f'select * from "{table_name}"').fetchall()
        chunks.extend(repr(tuple(row)) for row in rows)
    return "\n".join(chunks)
