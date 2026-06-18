import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from backend.app.storage.database import connect_sqlite, initialize_database


class DatabaseSchemaTests(unittest.TestCase):
    def test_initialize_database_creates_required_tables_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(db_path)
            try:
                initialize_database(connection)

                table_names = {
                    row["name"]
                    for row in connection.execute(
                        "select name from sqlite_master where type = 'table'"
                    ).fetchall()
                }
                foreign_keys_enabled = connection.execute("pragma foreign_keys").fetchone()[0]

                self.assertIn("games", table_names)
                self.assertIn("players", table_names)
                self.assertNotIn("llm_profiles", table_names)
                self.assertIn("game_events", table_names)
                self.assertIn("ai_decisions", table_names)
                self.assertIn("ai_memory_snapshots", table_names)
                self.assertEqual(foreign_keys_enabled, 1)
                player_columns = {
                    row["name"]
                    for row in connection.execute("pragma table_info(players)").fetchall()
                }
                self.assertIn("original_name", player_columns)
            finally:
                connection.close()

    def test_connect_sqlite_accepts_memory_database(self):
        connection = connect_sqlite(":memory:")
        try:
            self.assertIsInstance(connection, sqlite3.Connection)
            self.assertEqual(connection.execute("pragma foreign_keys").fetchone()[0], 1)
        finally:
            connection.close()

    def test_connection_can_be_used_from_fastapi_worker_thread(self):
        connection = connect_sqlite(":memory:")
        try:
            initialize_database(connection)
            errors = []

            def query_from_worker_thread():
                try:
                    connection.execute("select 1").fetchone()
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=query_from_worker_thread)
            thread.start()
            thread.join()

            self.assertEqual(errors, [])
        finally:
            connection.close()

    def test_initialize_database_migrates_legacy_llm_profile_foreign_keys(self):
        connection = connect_sqlite(":memory:")
        try:
            connection.executescript(
                """
                create table llm_profiles (
                    id text primary key,
                    name text not null,
                    base_url text not null,
                    api_key_encrypted_or_masked text not null,
                    model text not null,
                    temperature real not null,
                    max_tokens integer not null,
                    timeout real not null,
                    created_at text not null,
                    updated_at text not null
                );

                create table games (
                    id text primary key,
                    status text not null,
                    player_count integer not null,
                    role_set text not null,
                    current_round integer not null,
                    current_phase text not null,
                    winner text,
                    default_llm_profile_id text,
                    created_at text not null,
                    updated_at text not null,
                    foreign key(default_llm_profile_id) references llm_profiles(id)
                );

                create table players (
                    id text not null,
                    game_id text not null,
                    seat_index integer not null,
                    name text not null,
                    is_human integer not null,
                    role text not null,
                    faction text not null,
                    llm_profile_id text,
                    primary key(game_id, id),
                    foreign key(game_id) references games(id) on delete cascade,
                    foreign key(llm_profile_id) references llm_profiles(id),
                    unique(game_id, seat_index)
                );

                create table ai_decisions (
                    id integer primary key autoincrement,
                    game_id text not null,
                    player_id text not null,
                    phase text not null,
                    decision_type text not null,
                    input_summary text not null,
                    strategy_summary text not null,
                    output text not null,
                    model_name text not null,
                    llm_profile_id text,
                    prompt_template_name text not null,
                    prompt_template_version text not null,
                    context_builder_version text not null,
                    stable_prefix_hash text not null,
                    cache_strategy text not null,
                    context_summary text not null,
                    context_truncated integer not null,
                    output_raw text,
                    output_parsed text,
                    validation_status text not null,
                    created_at text not null,
                    foreign key(game_id) references games(id) on delete cascade,
                    foreign key(game_id, player_id) references players(game_id, id) on delete cascade,
                    foreign key(llm_profile_id) references llm_profiles(id)
                );

                insert into llm_profiles values (
                    'profile_1', 'DeepSeek', 'https://api.example.com/v1',
                    'legacy-test-key', 'deepseek-chat', 0.7, 1024, 30.0,
                    '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
                );
                insert into games values (
                    'game_1', 'active', 5, '[]', 1, 'team_proposal', null,
                    'profile_1', '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
                );
                insert into players values (
                    'player_1', 'game_1', 0, 'You', 1, 'merlin', 'good',
                    'profile_1'
                );
                insert into ai_decisions (
                    game_id, player_id, phase, decision_type, input_summary,
                    strategy_summary, output, model_name, llm_profile_id,
                    prompt_template_name, prompt_template_version,
                    context_builder_version, stable_prefix_hash, cache_strategy,
                    context_summary, context_truncated, output_raw,
                    output_parsed, validation_status, created_at
                ) values (
                    'game_1', 'player_1', 'speech', 'speak', '{}',
                    '{}', '{}', 'deepseek-chat', 'profile_1',
                    'avalon', 'v1', 'context-v1', 'hash',
                    'stable-prefix-v1', '{}', 0, null, null,
                    'ok', '2026-06-15T00:00:00Z'
                );
                """
            )

            initialize_database(connection)

            table_names = {
                row["name"]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
            game_profile_id = connection.execute(
                "select default_llm_profile_id from games where id = 'game_1'"
            ).fetchone()[0]
            player_profile_id = connection.execute(
                "select llm_profile_id from players where game_id = 'game_1' and id = 'player_1'"
            ).fetchone()[0]
            player_original_name = connection.execute(
                "select original_name from players where game_id = 'game_1' and id = 'player_1'"
            ).fetchone()[0]
            decision_profile_id = connection.execute(
                "select llm_profile_id from ai_decisions where game_id = 'game_1'"
            ).fetchone()[0]

            self.assertNotIn("llm_profiles", table_names)
            self.assertEqual(game_profile_id, "profile_1")
            self.assertEqual(player_profile_id, "profile_1")
            self.assertIsNone(player_original_name)
            self.assertEqual(decision_profile_id, "profile_1")
            for table_name in ("games", "players", "ai_decisions"):
                referenced_tables = [
                    row["table"]
                    for row in connection.execute(
                        f"pragma foreign_key_list({table_name})"
                    ).fetchall()
                ]
                self.assertNotIn("llm_profiles", referenced_tables)
        finally:
            connection.close()
