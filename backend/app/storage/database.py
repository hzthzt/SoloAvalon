from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from .schema import SCHEMA_SQL

DatabasePath = Union[str, Path]


def connect_sqlite(path: DatabasePath) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.commit()
    connection.execute("pragma foreign_keys = off")
    try:
        _migrate_legacy_llm_profile_references(connection)
        connection.executescript(SCHEMA_SQL)
        _ensure_column(connection, "games", "enabled_options", "enabled_options text not null default '[]'")
        _ensure_column(connection, "players", "original_name", "original_name text")
        _ensure_column(connection, "ai_decisions", "prompt_tokens", "prompt_tokens integer")
        _ensure_column(connection, "ai_decisions", "completion_tokens", "completion_tokens integer")
        _ensure_column(connection, "ai_decisions", "total_tokens", "total_tokens integer")
        _ensure_column(connection, "ai_decisions", "cached_tokens", "cached_tokens integer")
        _ensure_column(connection, "ai_decisions", "cache_hit_rate", "cache_hit_rate real")
        connection.execute("drop table if exists llm_profiles")
        connection.commit()
    finally:
        connection.execute("pragma foreign_keys = on")


def _migrate_legacy_llm_profile_references(connection: sqlite3.Connection) -> None:
    _rebuild_table_if_references_llm_profiles(
        connection,
        table_name="games",
        temp_table_sql="""
            create table games_new (
                id text primary key,
                status text not null,
                player_count integer not null,
                role_set text not null,
                enabled_options text not null default '[]',
                current_round integer not null,
                current_phase text not null,
                winner text,
                default_llm_profile_id text,
                created_at text not null,
                updated_at text not null
            )
        """,
        columns=(
            "id",
            "status",
            "player_count",
            "role_set",
            "current_round",
            "current_phase",
            "winner",
            "default_llm_profile_id",
            "created_at",
            "updated_at",
        ),
    )
    _rebuild_table_if_references_llm_profiles(
        connection,
        table_name="players",
        temp_table_sql="""
            create table players_new (
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
                unique(game_id, seat_index)
            )
        """,
        columns=(
            "id",
            "game_id",
            "seat_index",
            "name",
            "is_human",
            "role",
            "faction",
            "llm_profile_id",
        ),
    )
    _rebuild_table_if_references_llm_profiles(
        connection,
        table_name="ai_decisions",
        temp_table_sql="""
            create table ai_decisions_new (
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
                foreign key(game_id, player_id) references players(game_id, id) on delete cascade
            )
        """,
        columns=(
            "id",
            "game_id",
            "player_id",
            "phase",
            "decision_type",
            "input_summary",
            "strategy_summary",
            "output",
            "model_name",
            "llm_profile_id",
            "prompt_template_name",
            "prompt_template_version",
            "context_builder_version",
            "stable_prefix_hash",
            "cache_strategy",
            "context_summary",
            "context_truncated",
            "output_raw",
            "output_parsed",
            "validation_status",
            "created_at",
        ),
    )


def _rebuild_table_if_references_llm_profiles(
    connection: sqlite3.Connection,
    table_name: str,
    temp_table_sql: str,
    columns: tuple[str, ...],
) -> None:
    if not _table_exists(connection, table_name):
        return
    referenced_tables = {
        row["table"]
        for row in connection.execute(f"pragma foreign_key_list({table_name})").fetchall()
    }
    if "llm_profiles" not in referenced_tables:
        return

    temp_table_name = f"{table_name}_new"
    column_list = ", ".join(columns)
    connection.execute(f"drop table if exists {temp_table_name}")
    connection.execute(temp_table_sql)
    connection.execute(
        f"insert into {temp_table_name} ({column_list}) select {column_list} from {table_name}"
    )
    connection.execute(f"drop table {table_name}")
    connection.execute(f"alter table {temp_table_name} rename to {table_name}")


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    if not _table_exists(connection, table_name):
        return
    columns = {
        row["name"]
        for row in connection.execute(f"pragma table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    connection.execute(f"alter table {table_name} add column {column_definition}")
