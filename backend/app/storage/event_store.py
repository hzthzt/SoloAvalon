from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class EventRecord:
    id: int
    game_id: str
    event_index: int
    event_type: str
    public_payload: dict[str, Any]
    private_payload: dict[str, Any] | None
    created_at: str


class EventStore:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def append_event(
        self,
        game_id: str,
        event_type: str,
        public_payload: dict[str, Any],
        private_payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        event_index = self._next_event_index(game_id)
        created_at = _utc_now()
        cursor = self._connection.execute(
            """
            insert into game_events (
                game_id, event_index, event_type, public_payload, private_payload, created_at
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                event_index,
                event_type,
                _dump_json(public_payload),
                _dump_json(private_payload) if private_payload is not None else None,
                created_at,
            ),
        )
        self._connection.commit()
        return EventRecord(
            id=int(cursor.lastrowid),
            game_id=game_id,
            event_index=event_index,
            event_type=event_type,
            public_payload=public_payload,
            private_payload=private_payload,
            created_at=created_at,
        )

    def list_events(self, game_id: str) -> list[EventRecord]:
        rows = self._connection.execute(
            "select * from game_events where game_id = ? order by event_index",
            (game_id,),
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_events_after(self, game_id: str, event_index: int) -> list[EventRecord]:
        rows = self._connection.execute(
            """
            select * from game_events
            where game_id = ? and event_index > ?
            order by event_index
            """,
            (game_id, event_index),
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def export_game_log(
        self,
        game_id: str,
        include_private: bool = False,
    ) -> dict[str, Any]:
        game_row = self._connection.execute(
            "select * from games where id = ?",
            (game_id,),
        ).fetchone()
        if game_row is None:
            raise ValueError(f"unknown game id: {game_id}")

        exported_events = []
        for event in self.list_events(game_id):
            payload = {
                "event_index": event.event_index,
                "event_type": event.event_type,
                "public_payload": event.public_payload,
                "created_at": event.created_at,
            }
            if include_private:
                payload["private_payload"] = event.private_payload
            exported_events.append(payload)

        return {
            "game": {
                "id": game_row["id"],
                "status": game_row["status"],
                "player_count": game_row["player_count"],
                "current_round": game_row["current_round"],
                "current_phase": game_row["current_phase"],
                "winner": game_row["winner"],
                "created_at": game_row["created_at"],
                "updated_at": game_row["updated_at"],
            },
            "events": exported_events,
        }

    def _next_event_index(self, game_id: str) -> int:
        row = self._connection.execute(
            """
            select coalesce(max(event_index), 0) + 1 as next_index
            from game_events
            where game_id = ?
            """,
            (game_id,),
        ).fetchone()
        return int(row["next_index"])


def _event_from_row(row: sqlite3.Row) -> EventRecord:
    private_payload = row["private_payload"]
    return EventRecord(
        id=row["id"],
        game_id=row["game_id"],
        event_index=row["event_index"],
        event_type=row["event_type"],
        public_payload=json.loads(row["public_payload"]),
        private_payload=json.loads(private_payload) if private_payload is not None else None,
        created_at=row["created_at"],
    )


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

