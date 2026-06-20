from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.app.ai.player import AiDecisionError
from backend.app.services.event_visibility import (
    hide_unsettled_vote_values,
    public_event_dicts,
)
from backend.app.services.game_service import GameService
from .models import CreateGameRequest, HumanActionRequest

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    APIRouter = None
    HTTPException = None


class GamesApi:
    def __init__(self, service: GameService):
        self._service = service

    def create_game(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = CreateGameRequest.from_payload(payload)
        return self._service.create_game(
            player_count=request.player_count,
            enabled_options=request.enabled_options,
            human_name=request.human_name,
            ai_names=request.ai_names,
            default_llm_profile_id=request.default_llm_profile_id,
            ai_profile_overrides=request.ai_profile_overrides,
        )

    def list_games(self) -> list[dict[str, Any]]:
        return [asdict(summary) for summary in self._service.list_games()]

    def get_game(self, game_id: str) -> dict[str, Any]:
        return self._service.get_game_state(game_id)

    def submit_action(self, game_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = HumanActionRequest.from_payload(payload)
        return self._service.submit_human_action(game_id, request.action_type, request.payload)

    def submit_human_ai_action(self, game_id: str) -> dict[str, Any]:
        return self._service.submit_human_ai_action(game_id)

    def list_events(self, game_id: str, include_private: bool = False) -> list[dict[str, Any]]:
        events = self._service.list_events(game_id)
        if include_private:
            return [asdict(event) for event in events]
        return public_event_dicts(events)

    def export_game(self, game_id: str, include_private: bool = False) -> dict[str, Any]:
        exported = self._service.export_game_log(game_id, include_private=include_private)
        if not include_private:
            exported = dict(exported)
            exported["events"] = hide_unsettled_vote_values(exported["events"])
        return exported

    def get_room_detail(self, game_id: str) -> dict[str, Any]:
        return self._service.get_room_detail(game_id)

    def archive_game(self, game_id: str) -> dict[str, Any]:
        return asdict(self._service.archive_game(game_id))

    def delete_game(self, game_id: str) -> dict[str, str]:
        self._service.delete_game(game_id)
        return {"status": "deleted"}


def build_games_router(service: GameService):
    if APIRouter is None:
        return None
    api = GamesApi(service)
    router = APIRouter(prefix="/api/games", tags=["games"])

    @router.post("")
    def create_game(payload: dict[str, Any]):
        return _call(lambda: api.create_game(payload))

    @router.get("")
    def list_games():
        return _call(api.list_games)

    @router.get("/{game_id}")
    def get_game(game_id: str):
        return _call(lambda: api.get_game(game_id))

    @router.post("/{game_id}/actions")
    def submit_action(game_id: str, payload: dict[str, Any]):
        return _call(lambda: api.submit_action(game_id, payload))

    @router.post("/{game_id}/ai-actions/human")
    def submit_human_ai_action(game_id: str):
        return _call(lambda: api.submit_human_ai_action(game_id))

    @router.get("/{game_id}/events")
    def list_events(game_id: str, include_private: bool = False):
        return _call(lambda: api.list_events(game_id, include_private=include_private))

    @router.get("/{game_id}/export")
    def export_game(game_id: str, include_private: bool = False):
        return _call(lambda: api.export_game(game_id, include_private=include_private))

    @router.get("/{game_id}/room")
    def get_room_detail(game_id: str):
        return _call(lambda: api.get_room_detail(game_id))

    @router.post("/{game_id}/archive")
    def archive_game(game_id: str):
        return _call(lambda: api.archive_game(game_id))

    @router.delete("/{game_id}")
    def delete_game(game_id: str):
        return _call(lambda: api.delete_game(game_id))

    return router


def _call(handler):
    try:
        return handler()
    except AiDecisionError as exc:
        if HTTPException is None:
            raise
        raise HTTPException(
            status_code=_ai_decision_status_code(exc),
            detail=f"AI 决策失败：{exc.error_message}",
        ) from exc
    except ValueError as exc:
        if HTTPException is None:
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _ai_decision_status_code(error: AiDecisionError) -> int:
    if error.error_type == "TimeoutError":
        return 504
    return 502
