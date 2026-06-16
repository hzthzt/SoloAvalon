from __future__ import annotations

import os
from pathlib import Path

from backend.app.api.games import build_games_router
from backend.app.api.llm_profiles import build_llm_profiles_router
from backend.app.services.game_service import GameService
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.llm_profile_repository import LlmProfileRepository

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    class FastAPI:
        def __init__(self, title: str):
            self.title = title

        def include_router(self, router):
            return None

        def add_middleware(self, *args, **kwargs):
            return None

    CORSMiddleware = None


def _database_path() -> str:
    return os.environ.get(
        "SOLOAVALON_DB",
        str(Path(__file__).resolve().parents[2] / "soloavalon.sqlite3"),
    )


connection = connect_sqlite(_database_path())
initialize_database(connection)
game_service = GameService(connection)
llm_profile_repository = LlmProfileRepository(connection)

app = FastAPI(title="SoloAvalon")

if CORSMiddleware is not None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

for router in (
    build_games_router(game_service),
    build_llm_profiles_router(llm_profile_repository),
):
    if router is not None:
        app.include_router(router)
