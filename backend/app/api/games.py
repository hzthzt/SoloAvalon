from __future__ import annotations

import json
from typing import Any

from backend.app.ai.player import AiDecisionError
from backend.app.services.game_service import GameService
from .errors import error_detail
from .models import CreateGameRequest, HumanActionRequest, expected_revision, GameView, GameSummaryView, RoomReview
from backend.app.services.game_work import GameConflict

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import StreamingResponse
except ImportError:
    APIRouter = None
    HTTPException = None
    Request = None
    StreamingResponse = None


SSE_HEARTBEAT_SECONDS = 15.0


class GamesApi:
    def __init__(self, service: GameService):
        self._service = service

    def create_game(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = CreateGameRequest.from_payload(payload)
        return self._service.application.create_game(
            player_count=request.player_count, enabled_options=request.enabled_options,
            human_name=request.human_name, ai_names=request.ai_names,
            default_llm_profile_id=request.default_llm_profile_id,
            ai_profile_overrides=request.ai_profile_overrides,
        )

    def list_games(self):
        return self._service.queries.list_games()

    def get_game(self, game_id):
        return self._service.queries.get_game(game_id)

    def submit_action(self, game_id, payload):
        request = HumanActionRequest.from_payload(payload)
        return self._service.application.submit_human_action(
            game_id, request.expected_revision, request.action_type, request.payload,
        )

    def submit_human_ai_action(self, game_id, payload):
        return self._service.application.request_human_ai(game_id, expected_revision(payload))

    def retry_paused_game(self, game_id):
        return self._service.application.resume_game(game_id)

    def list_events(self, game_id, include_private=False):
        return self.list_events_after(game_id, 0, include_private=include_private)

    def list_events_after(self, game_id, event_index, include_private=False):
        queries = self._service.queries
        if include_private:
            return queries.get_private_events(game_id, event_index)
        return queries.get_public_events(game_id, event_index)

    def export_game(self, game_id, include_private=False):
        return self._service.queries.export_game(game_id, include_private)

    def get_room_detail(self, game_id):
        return self._service.queries.get_completed_review(game_id)

    def archive_game(self, game_id):
        return self._service.application.archive_game(game_id)

    def delete_game(self, game_id):
        self._service.application.delete_game(game_id)
        return {"status": "deleted"}


def build_games_router(service: GameService):
    if APIRouter is None:
        return None
    api = GamesApi(service)
    router = APIRouter(prefix="/api/games", tags=["games"])

    @router.post("", response_model=GameView)
    def create_game(payload: dict[str, Any]):
        return _call(lambda: api.create_game(payload))

    @router.get("", response_model=list[GameSummaryView])
    def list_games():
        return _call(api.list_games)

    @router.get("/{game_id}", response_model=GameView)
    def get_game(game_id: str):
        return _call(lambda: api.get_game(game_id))

    @router.post("/{game_id}/actions", response_model=GameView)
    def submit_action(game_id: str, payload: dict[str, Any]):
        return _call(lambda: api.submit_action(game_id, payload))

    @router.post("/{game_id}/ai-actions/human", response_model=GameView)
    def submit_human_ai_action(game_id: str, payload: dict[str, Any]):
        return _call(lambda: api.submit_human_ai_action(game_id, payload))

    @router.post("/{game_id}/retry", response_model=GameView)
    def retry_paused_game(game_id: str):
        return _call(lambda: api.retry_paused_game(game_id))

    @router.get("/{game_id}/events")
    def list_events(game_id: str, include_private: bool = False, after: int = 0):
        return _call(lambda: api.list_events_after(game_id, after, include_private=include_private))

    @router.get("/{game_id}/events/stream")
    async def stream_events(game_id: str, request: Request, after: int = 0):
        _call(lambda: api.get_game(game_id))
        subscription = service.event_notifier.subscribe(game_id)

        return StreamingResponse(
            game_event_stream_frames(
                api,
                game_id,
                request,
                subscription,
                after=after,
                heartbeat_seconds=SSE_HEARTBEAT_SECONDS,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/{game_id}/export")
    def export_game(game_id: str, include_private: bool = False):
        return _call(lambda: api.export_game(game_id, include_private=include_private))

    @router.get("/{game_id}/room", response_model=RoomReview)
    def get_room_detail(game_id: str):
        return _call(lambda: api.get_room_detail(game_id))

    @router.post("/{game_id}/archive", response_model=GameSummaryView)
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
        message = f"AI 决策失败：{exc.error_message}"
        raise HTTPException(
            status_code=_ai_decision_status_code(exc),
            detail=error_detail(exc, message),
        ) from exc
    except GameConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "current_revision": exc.current_revision,
        }) from exc
    except (ValueError, KeyError, TypeError) as exc:
        if HTTPException is None:
            raise
        raise HTTPException(status_code=400, detail=error_detail(exc)) from exc
    except Exception as exc:
        if HTTPException is None:
            raise
        raise HTTPException(status_code=500, detail=error_detail(exc)) from exc


async def game_event_stream_frames(
    api: GamesApi,
    game_id: str,
    request: Any,
    subscription: Any,
    *,
    after: int = 0,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
):
    latest_event_index = after
    latest_revision = -1
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                state = api.get_game(game_id)
            except ValueError:
                break
            if state["revision"] != latest_revision:
                latest_revision = state["revision"]
                payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                yield f"event: game-state\ndata: {payload}\n\n"
            events = api.list_events_after(game_id, latest_event_index)
            if events:
                for event in events:
                    latest_event_index = max(latest_event_index, int(event["event_index"]))
                    yield sse_event_frame(event)
                continue
            notified = await subscription.wait(timeout=heartbeat_seconds)
            if not notified:
                yield sse_keep_alive_frame()
    finally:
        subscription.close()


def sse_event_frame(event: dict[str, Any]) -> str:
    event_index = int(event["event_index"])
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"id: {event_index}\nevent: game-event\ndata: {payload}\n\n"


def sse_keep_alive_frame() -> str:
    return ": keep-alive\n\n"


def _ai_decision_status_code(error: AiDecisionError) -> int:
    if error.error_type in {"TimeoutError", "APITimeoutError"}:
        return 504
    return 502
