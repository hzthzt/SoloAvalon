# Storage and Event Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SQLite persistence for games, players, and append-only game events so the backend can save, list, delete, and export local Avalon match logs.

**Architecture:** Keep persistence behind `backend/app/storage/` repositories so the deterministic rules engine remains pure and testable. Store public and private event payloads separately, default exports to public-safe data only, and use SQLite foreign keys plus cascading deletes for local log management.

**Tech Stack:** Python 3.10, standard-library `sqlite3`, standard-library `unittest`, JSON payloads stored as text, UTC ISO-8601 timestamps.

---

## File Structure

- `backend/app/storage/database.py`: opens SQLite connections, enables foreign keys, sets row access, and runs schema creation.
- `backend/app/storage/schema.py`: owns the SQL schema for `games`, `players`, `llm_profiles`, `game_events`, `ai_decisions`, and `ai_memory_snapshots`.
- `backend/app/storage/game_repository.py`: saves a new `GameState`, lists game summaries, loads players, and deletes games.
- `backend/app/storage/event_store.py`: appends event records with sequential indexes, lists events, and exports public-safe JSON.
- `backend/app/storage/__init__.py`: package marker and convenience exports.
- `tests/storage/`: persistence tests using temporary SQLite files.

### Task 1: SQLite Connection and Schema

**Files:**
- Create: `backend/app/storage/__init__.py`
- Create: `backend/app/storage/database.py`
- Create: `backend/app/storage/schema.py`
- Create: `tests/storage/__init__.py`
- Test: `tests/storage/test_database_schema.py`

- [ ] **Step 1: Write the failing schema test**

Create `tests/storage/test_database_schema.py`:

```python
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.storage.database import connect_sqlite, initialize_database


class DatabaseSchemaTests(unittest.TestCase):
    def test_initialize_database_creates_required_tables_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(db_path)
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
            self.assertIn("llm_profiles", table_names)
            self.assertIn("game_events", table_names)
            self.assertIn("ai_decisions", table_names)
            self.assertIn("ai_memory_snapshots", table_names)
            self.assertEqual(foreign_keys_enabled, 1)

    def test_connect_sqlite_accepts_memory_database(self):
        connection = connect_sqlite(":memory:")

        self.assertIsInstance(connection, sqlite3.Connection)
        self.assertEqual(connection.execute("pragma foreign_keys").fetchone()[0], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_database_schema -v`
Expected: FAIL because `backend.app.storage.database` does not exist.

- [ ] **Step 3: Write minimal schema implementation**

Create `backend/app/storage/schema.py`:

```python
SCHEMA_SQL = """
create table if not exists llm_profiles (
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

create table if not exists games (
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

create table if not exists players (
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

create table if not exists game_events (
    id integer primary key autoincrement,
    game_id text not null,
    event_index integer not null,
    event_type text not null,
    public_payload text not null,
    private_payload text,
    created_at text not null,
    foreign key(game_id) references games(id) on delete cascade,
    unique(game_id, event_index)
);

create table if not exists ai_decisions (
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

create table if not exists ai_memory_snapshots (
    id integer primary key autoincrement,
    game_id text not null,
    player_id text not null,
    round_number integer not null,
    phase text not null,
    memory_payload text not null,
    created_at text not null,
    foreign key(game_id) references games(id) on delete cascade,
    foreign key(game_id, player_id) references players(game_id, id) on delete cascade
);
"""
```

Create `backend/app/storage/database.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

from .schema import SCHEMA_SQL

DatabasePath = Union[str, Path]


def connect_sqlite(path: DatabasePath) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()
```

Create `backend/app/storage/__init__.py`:

```python
from .database import connect_sqlite, initialize_database

__all__ = ["connect_sqlite", "initialize_database"]
```

Create empty package marker `tests/storage/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_database_schema -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage tests/storage/test_database_schema.py tests/storage/__init__.py
git commit -m "feat: add sqlite schema initialization"
```

### Task 2: Game Repository

**Files:**
- Create: `backend/app/storage/game_repository.py`
- Modify: `backend/app/storage/__init__.py`
- Test: `tests/storage/test_game_repository.py`

- [ ] **Step 1: Write the failing repository test**

Create `tests/storage/test_game_repository.py`:

```python
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

    def test_list_games_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            initialize_database(connection)
            repository = GameRepository(connection)

            repository.save_new_game(create_five_player_game(seed=1), game_id="game_old")
            repository.save_new_game(create_five_player_game(seed=2), game_id="game_new")

            summaries = repository.list_games()

            self.assertEqual([summary.id for summary in summaries], ["game_new", "game_old"])

    def test_delete_game_removes_players_by_cascade(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            initialize_database(connection)
            repository = GameRepository(connection)

            repository.save_new_game(create_five_player_game(seed=3), game_id="game_1")
            repository.delete_game("game_1")

            self.assertIsNone(repository.get_game_summary("game_1"))
            self.assertEqual(repository.list_players("game_1"), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_game_repository -v`
Expected: FAIL because `GameRepository` does not exist.

- [ ] **Step 3: Implement the repository**

Create `backend/app/storage/game_repository.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.game.models import GameState


@dataclass(frozen=True)
class GameSummary:
    id: str
    status: str
    player_count: int
    role_set: list[str]
    current_round: int
    current_phase: str
    winner: str | None
    default_llm_profile_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredPlayer:
    id: str
    game_id: str
    seat_index: int
    name: str
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
        default_llm_profile_id: str | None = None,
    ) -> None:
        now = _utc_now()
        status = "complete" if state.winner is not None else "active"
        role_set = json.dumps([player.role.value for player in state.players], sort_keys=True)
        self._connection.execute(
            """
            insert into games (
                id, status, player_count, role_set, current_round, current_phase,
                winner, default_llm_profile_id, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                status,
                len(state.players),
                role_set,
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
                id, game_id, seat_index, name, is_human, role, faction, llm_profile_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    player.id,
                    game_id,
                    player.seat_index,
                    player.name,
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


def _game_summary_from_row(row: sqlite3.Row) -> GameSummary:
    return GameSummary(
        id=row["id"],
        status=row["status"],
        player_count=row["player_count"],
        role_set=json.loads(row["role_set"]),
        current_round=row["current_round"],
        current_phase=row["current_phase"],
        winner=row["winner"],
        default_llm_profile_id=row["default_llm_profile_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
```

Update `backend/app/storage/__init__.py`:

```python
from .database import connect_sqlite, initialize_database
from .game_repository import GameRepository, GameSummary, StoredPlayer

__all__ = [
    "GameRepository",
    "GameSummary",
    "StoredPlayer",
    "connect_sqlite",
    "initialize_database",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_game_repository -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage tests/storage/test_game_repository.py
git commit -m "feat: persist game summaries and players"
```

### Task 3: Append-Only Event Store

**Files:**
- Create: `backend/app/storage/event_store.py`
- Modify: `backend/app/storage/__init__.py`
- Test: `tests/storage/test_event_store.py`

- [ ] **Step 1: Write the failing event store test**

Create `tests/storage/test_event_store.py`:

```python
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
            self.assertEqual([event.event_type for event in store.list_events("game_1")], [
                "game_created",
                "leader_changed",
            ])

    def test_export_game_log_excludes_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_event_store -v`
Expected: FAIL because `EventStore` does not exist.

- [ ] **Step 3: Implement event storage and export**

Create `backend/app/storage/event_store.py`:

```python
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
            "select coalesce(max(event_index), 0) + 1 as next_index from game_events where game_id = ?",
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
```

Update `backend/app/storage/__init__.py`:

```python
from .database import connect_sqlite, initialize_database
from .event_store import EventRecord, EventStore
from .game_repository import GameRepository, GameSummary, StoredPlayer

__all__ = [
    "EventRecord",
    "EventStore",
    "GameRepository",
    "GameSummary",
    "StoredPlayer",
    "connect_sqlite",
    "initialize_database",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_event_store -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage tests/storage/test_event_store.py
git commit -m "feat: add append-only game event store"
```

### Task 4: Event Payload Builders

**Files:**
- Create: `backend/app/game/events.py`
- Test: `tests/game/test_event_payloads.py`

- [ ] **Step 1: Write failing tests for public and private payload builders**

Create `tests/game/test_event_payloads.py`:

```python
import unittest

from backend.app.game.events import (
    build_game_created_payloads,
    build_private_view_payloads,
    build_roles_assigned_payloads,
)
from backend.app.game.models import Role
from backend.app.game.rules import create_five_player_game


class EventPayloadTests(unittest.TestCase):
    def test_game_created_payload_has_public_game_shape(self):
        state = create_five_player_game(seed=30)

        public_payload, private_payload = build_game_created_payloads(state, game_id="game_1")

        self.assertEqual(public_payload["game_id"], "game_1")
        self.assertEqual(public_payload["player_count"], 5)
        self.assertEqual(public_payload["current_round"], 1)
        self.assertEqual(public_payload["current_phase"], "team_proposal")
        self.assertIsNone(private_payload)

    def test_roles_assigned_payload_keeps_truth_private(self):
        state = create_five_player_game(seed=31)

        public_payload, private_payload = build_roles_assigned_payloads(state)

        self.assertEqual(public_payload, {"player_count": 5})
        self.assertEqual(set(private_payload["roles_by_player_id"]), {
            "player_1",
            "player_2",
            "player_3",
            "player_4",
            "player_5",
        })

    def test_private_view_payload_contains_only_legal_view(self):
        state = create_five_player_game(seed=32)
        loyal = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)

        public_payload, private_payload = build_private_view_payloads(state, loyal.id)

        self.assertEqual(public_payload["viewer_player_id"], loyal.id)
        self.assertEqual(private_payload["visible_roles"][loyal.id], Role.LOYAL_SERVANT.value)
        hidden_other_roles = [
            role
            for player_id, role in private_payload["visible_roles"].items()
            if player_id != loyal.id
        ]
        self.assertEqual(hidden_other_roles, [None, None, None, None])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_event_payloads -v`
Expected: FAIL because `backend.app.game.events` does not exist.

- [ ] **Step 3: Implement payload builders**

Create `backend/app/game/events.py`:

```python
from __future__ import annotations

from typing import Any

from .models import GameState, Role
from .rules import private_view_for_player


def build_game_created_payloads(
    state: GameState,
    game_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return (
        {
            "game_id": game_id,
            "player_count": len(state.players),
            "current_round": state.current_round,
            "current_phase": state.phase.value,
        },
        None,
    )


def build_roles_assigned_payloads(
    state: GameState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"player_count": len(state.players)},
        {
            "roles_by_player_id": {
                player.id: player.role.value
                for player in state.players
            },
            "factions_by_player_id": {
                player.id: player.faction.value
                for player in state.players
            },
        },
    )


def build_private_view_payloads(
    state: GameState,
    viewer_player_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    view = private_view_for_player(state, viewer_player_id)
    visible_roles = {
        player_id: _role_value(role)
        for player_id, role in view.visible_roles.items()
    }
    return (
        {"viewer_player_id": viewer_player_id},
        {
            "viewer_player_id": viewer_player_id,
            "known_evil_player_ids": view.known_evil_player_ids,
            "visible_roles": visible_roles,
        },
    )


def _role_value(role: Role | None) -> str | None:
    return role.value if role is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_event_payloads -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/game/events.py tests/game/test_event_payloads.py
git commit -m "feat: build storage-safe event payloads"
```

### Task 5: Full Storage Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document storage verification**

Update `README.md` with this storage note:

```markdown
The storage layer uses SQLite through the Python standard library. Tests create temporary database files and do not write match logs into the repository.
```

- [ ] **Step 2: Run focused storage tests**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests/storage -v`
Expected: PASS.

- [ ] **Step 3: Run full backend tests**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document sqlite storage tests"
```

## Self-Review

- Spec coverage: This plan covers the design document's SQLite tables, game/player persistence, event stream storage, public/private event payload separation, deletion support, and JSON export. It does not cover LLM profile CRUD screens or FastAPI routes because those are separate implementation slices.
- Placeholder scan: The plan uses concrete files, commands, test code, implementation code, and commit messages.
- Type consistency: `GameRepository`, `EventStore`, `GameSummary`, `StoredPlayer`, `EventRecord`, and payload builder names are introduced before use and reused consistently.
