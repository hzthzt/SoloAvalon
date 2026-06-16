from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AiMemorySnapshotInput:
    game_id: str
    player_id: str
    round_number: int
    phase: str
    memory_payload: dict[str, Any]


@dataclass(frozen=True)
class StoredAiMemorySnapshot:
    id: int
    game_id: str
    player_id: str
    round_number: int
    phase: str
    memory_payload: dict[str, Any]
    created_at: str


class AiMemoryRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def save_snapshot(self, snapshot: AiMemorySnapshotInput) -> StoredAiMemorySnapshot:
        _validate_memory_payload(snapshot.memory_payload)
        created_at = _utc_now()
        cursor = self._connection.execute(
            """
            insert into ai_memory_snapshots (
                game_id, player_id, round_number, phase, memory_payload, created_at
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.game_id,
                snapshot.player_id,
                snapshot.round_number,
                snapshot.phase,
                _dump_json(snapshot.memory_payload),
                created_at,
            ),
        )
        self._connection.commit()
        saved = self.get_snapshot(int(cursor.lastrowid))
        if saved is None:
            raise RuntimeError("failed to save ai memory snapshot")
        return saved

    def get_snapshot(self, snapshot_id: int) -> StoredAiMemorySnapshot | None:
        row = self._connection.execute(
            "select * from ai_memory_snapshots where id = ?",
            (snapshot_id,),
        ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def list_snapshots(
        self,
        game_id: str,
        player_id: str | None = None,
    ) -> list[StoredAiMemorySnapshot]:
        if player_id is None:
            rows = self._connection.execute(
                "select * from ai_memory_snapshots where game_id = ? order by id",
                (game_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                select * from ai_memory_snapshots
                where game_id = ? and player_id = ?
                order by id
                """,
                (game_id, player_id),
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]


def _snapshot_from_row(row: sqlite3.Row) -> StoredAiMemorySnapshot:
    return StoredAiMemorySnapshot(
        id=row["id"],
        game_id=row["game_id"],
        player_id=row["player_id"],
        round_number=row["round_number"],
        phase=row["phase"],
        memory_payload=json.loads(row["memory_payload"]),
        created_at=row["created_at"],
    )


def _validate_memory_payload(payload: dict[str, Any]) -> None:
    required_keys = {
        "suspicions",
        "trusted_players",
        "key_observations",
        "merlin_candidates",
    }
    missing = required_keys - set(payload)
    if missing:
        raise ValueError(f"memory payload missing keys: {sorted(missing)}")
    if not isinstance(payload["suspicions"], dict):
        raise ValueError("suspicions must be an object")
    if not isinstance(payload["trusted_players"], list):
        raise ValueError("trusted_players must be a list")
    if not isinstance(payload["key_observations"], list):
        raise ValueError("key_observations must be a list")
    if not isinstance(payload["merlin_candidates"], list):
        raise ValueError("merlin_candidates must be a list")


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
