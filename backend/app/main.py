from __future__ import annotations

import os
from pathlib import Path

from backend.app.api.games import build_games_router
from backend.app.api.llm_profiles import build_llm_profiles_router
from backend.app.services.game_service import GameService

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _database_path() -> str:
    return os.environ.get(
        "SOLOAVALON_DB",
        str(Path(__file__).resolve().parents[2] / "soloavalon.sqlite3"),
    )


def _frontend_origins() -> list[str]:
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    configured_origin = os.environ.get("SOLOAVALON_FRONTEND_ORIGIN")
    if configured_origin and configured_origin not in origins:
        origins.append(configured_origin)
    return origins


def create_app(database_path=None, *, ai_player=None, profiles=None, **runner_options) -> FastAPI:
    services = GameService(database_path if database_path is not None else _database_path(),
                           ai_player=ai_player, profiles=profiles, **runner_options)

    @asynccontextmanager
    async def lifespan(app):
        services.runner.start()
        try:
            yield
        finally:
            services.runner.stop()
            services.database.close()

    app = FastAPI(title="SoloAvalon", lifespan=lifespan)
    app.state.services = services
    app.add_middleware(
        CORSMiddleware, allow_origins=_frontend_origins(), allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(build_games_router(services))
    app.include_router(build_llm_profiles_router(services.profiles))
    return app


app = create_app()
