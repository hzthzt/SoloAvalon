from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from backend.app.game.models import GameState


@dataclass(frozen=True)
class GameSummary:
    id: str
    display_name: str
    status: str
    player_count: int
    role_set: list[str]
    enabled_options: list[str]
    current_round: int
    current_phase: str
    winner: str | None
    default_llm_profile_id: str | None
    archived_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredPlayer:
    id: str
    game_id: str
    seat_index: int
    name: str
    original_name: str | None
    is_human: bool
    role: str
    faction: str
    llm_profile_id: str | None


class GameRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def save_new_game(
        self,
        state: GameState,
        game_id: str,
        display_name: str | None = None,
        default_llm_profile_id: str | None = None,
    ) -> None:
        now = _utc_now()
        status = "complete" if state.winner is not None else "active"
        role_set = json.dumps(
            [player.role.value for player in state.players],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        enabled_options = json.dumps(
            sorted(option.value for option in state.enabled_options),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._connection.execute(
            """
            insert into games (
                id, display_name, status, player_count, role_set, enabled_options, current_round, current_phase,
                winner, default_llm_profile_id, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                display_name or game_id,
                status,
                len(state.players),
                role_set,
                enabled_options,
                state.current_round,
                state.phase.value,
                state.winner.value if state.winner else None,
                default_llm_profile_id,
                now,
                now,
            ),
        )
        self._connection.executemany(
            """
            insert into players (
                id, game_id, seat_index, name, original_name, is_human, role, faction, llm_profile_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    player.id,
                    game_id,
                    player.seat_index,
                    player.name,
                    player.original_name,
                    1 if player.is_human else 0,
                    player.role.value,
                    player.faction.value,
                    player.llm_profile_id,
                )
                for player in state.players
            ],
        )
        self._connection.commit()

    def get_game_summary(self, game_id: str) -> GameSummary | None:
        row = self._connection.execute(
            "select * from games where id = ?",
            (game_id,),
        ).fetchone()
        if row is None:
            return None
        return _game_summary_from_row(row)

    def list_games(self) -> list[GameSummary]:
        rows = self._connection.execute(
            "select * from games order by created_at desc, rowid desc"
        ).fetchall()
        return [_game_summary_from_row(row) for row in rows]

    def next_game_id(self) -> str:
        rows = self._connection.execute("select id from games").fetchall()
        numbers = _matching_numbers((row["id"] for row in rows), r"game_(\d+)")
        return f"game_{max(numbers, default=0) + 1}"

    def next_room_display_name(self) -> str:
        rows = self._connection.execute("select id, display_name from games").fetchall()
        values = []
        for row in rows:
            values.append(row["id"])
            if "display_name" in row.keys():
                values.append(row["display_name"])
        numbers = _matching_numbers(values, r"游戏#(\d+)")
        return f"游戏#{max(numbers, default=0) + 1}"

    def list_players(self, game_id: str) -> list[StoredPlayer]:
        rows = self._connection.execute(
            "select * from players where game_id = ? order by seat_index",
            (game_id,),
        ).fetchall()
        return [
            StoredPlayer(
                id=row["id"],
                game_id=row["game_id"],
                seat_index=row["seat_index"],
                name=row["name"],
                original_name=row["original_name"],
                is_human=bool(row["is_human"]),
                role=row["role"],
                faction=row["faction"],
                llm_profile_id=row["llm_profile_id"],
            )
            for row in rows
        ]

    def delete_game(self, game_id: str) -> None:
        self._connection.execute("delete from games where id = ?", (game_id,))
        self._connection.commit()

    def update_game_state(self, game_id: str, state: GameState) -> None:
        status = "complete" if state.winner is not None else "active"
        cursor = self._connection.execute(
            """
            update games
            set status = ?,
                current_round = ?,
                current_phase = ?,
                winner = ?,
                updated_at = ?
            where id = ?
            """,
            (
                status,
                state.current_round,
                state.phase.value,
                state.winner.value if state.winner else None,
                _utc_now(),
                game_id,
            ),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"unknown game id: {game_id}")

    def update_game_status(self, game_id: str, status: str) -> None:
        cursor = self._connection.execute(
            """
            update games
            set status = ?,
                updated_at = ?
            where id = ?
            """,
            (status, _utc_now(), game_id),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"unknown game id: {game_id}")

    def archive_game(self, game_id: str) -> GameSummary:
        current = self.get_game_summary(game_id)
        if current is None:
            raise ValueError(f"unknown game id: {game_id}")
        if current.archived_at is not None:
            return current
        archived_at = _utc_now()
        self._connection.execute(
            """
            update games
            set archived_at = ?,
                updated_at = ?
            where id = ?
            """,
            (archived_at, archived_at, game_id),
        )
        self._connection.commit()
        archived = self.get_game_summary(game_id)
        if archived is None:
            raise RuntimeError("failed to archive game")
        return archived

    def set_player_llm_profile(
        self,
        game_id: str,
        player_id: str,
        llm_profile_id: str | None,
    ) -> None:
        cursor = self._connection.execute(
            """
            update players
            set llm_profile_id = ?
            where game_id = ? and id = ?
            """,
            (llm_profile_id, game_id, player_id),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            raise ValueError(f"unknown player id for game: {game_id}/{player_id}")


def _matching_numbers(values: Iterable[str | None], pattern: str) -> list[int]:
    numbers = []
    for value in values:
        if value is None:
            continue
        match = re.fullmatch(pattern, value)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _game_summary_from_row(row: sqlite3.Row) -> GameSummary:
    return GameSummary(
        id=row["id"],
        display_name=row["display_name"],
        status=row["status"],
        player_count=row["player_count"],
        role_set=json.loads(row["role_set"]),
        enabled_options=json.loads(row["enabled_options"]),
        current_round=row["current_round"],
        current_phase=row["current_phase"],
        winner=row["winner"],
        default_llm_profile_id=row["default_llm_profile_id"],
        archived_at=row["archived_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
