from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.ai.player import AiPlayer
from backend.app.api.games import GamesApi, build_games_router, game_event_stream_frames, sse_event_frame, sse_keep_alive_frame
from backend.app.llm.profiles import LlmProfileInput
from backend.app.llm.provider import LlmProvider
from backend.app.services.game_service import GameService
from backend.app.services.game_work import GameUnitOfWork
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from tests.game_support import _DeterministicProvider, drain, play_to_completion, wait_until


class GamesApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        self.profiles = LlmProfileRepository(Path(self.temp.name) / "profiles.json")
        self.profiles.create_profile("test", LlmProfileInput(
            name="Test", base_url="https://example.test/v1", api_key="test-key", model="fake-model",
            timeout=1, transport_retries=0, parse_retries=0,
        ))
        self.service = GameService(":memory:", profiles=self.profiles,
            ai_player=AiPlayer(provider=LlmProvider(transport=httpx.MockTransport(self.transport))))
        self.addCleanup(self.service.database.close)
        self.addCleanup(self.service.runner.stop)
        self.app = FastAPI()
        self.app.include_router(build_games_router(self.service))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.api = GamesApi(self.service)

    def transport(self, request):
        self.requests.append(request)
        messages = json.loads(request.content)["messages"]
        content = _DeterministicProvider()._content(None, messages)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100,
                      "prompt_tokens_details": {"cached_tokens": 40}}})

    def create(self):
        return self.service.application.create_game(seed=2, default_llm_profile_id="test")

    def test_http_create_and_queries_are_whitelisted_and_do_not_run_models(self):
        response = self.client.post("/api/games", json={"default_llm_profile_id": "test", "seed": 999})
        self.assertEqual(response.status_code, 200)
        game = response.json()
        path = f"/api/games/{game['id']}"
        self.assertEqual(self.client.get(path).json(), game)
        self.assertNotIn("role_set", self.client.get("/api/games").json()[0])
        self.assertNotIn("default_llm_profile_id", self.client.get("/api/games").json()[0])
        self.assertEqual(self.requests, [])
        public = self.client.get(path + "/events").json()
        self.assertEqual([event["event_index"] for event in public], [1, 2])
        self.assertTrue(all("private_payload" not in event for event in public))
        self.assertNotIn("roles_by_player_id", self.client.get(path + "/export").text)

    def test_actions_require_revision_and_stale_version_returns_409_with_current_revision(self):
        game = self.create()
        path = f"/api/games/{game['id']}"
        for revision in (None, -1, True, "1", 1.2):
            payload = {"action_type": "propose_team", "team": ["player_1", "player_2"]}
            if revision is not None: payload["expected_revision"] = revision
            self.assertEqual(self.client.post(path + "/actions", json=payload).status_code, 400)
            self.assertEqual(self.client.post(path + "/ai-actions/human", json=payload).status_code, 400)
        payload = {"expected_revision": game["revision"], "action_type": "propose_team", "team": ["player_1", "player_2"]}
        response = self.client.post(path + "/actions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], "speech")
        conflict = self.client.post(path + "/actions", json=payload)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["current_revision"], response.json()["revision"])
        self.assertEqual(self.requests, [])

    def test_invalid_actions_are_400_and_do_not_change_revision(self):
        game = self.create()
        path = f"/api/games/{game['id']}/actions"
        for action in ({"action_type": "speak", "message": "wrong phase"},
                       {"action_type": "propose_team"},
                       {"action_type": "propose_team", "team": ["missing"]}):
            response = self.client.post(path, json=action | {"expected_revision": game["revision"]})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api.get_game(game["id"]), game)

    def test_model_failures_publish_safe_persistent_pause_reasons(self):
        cases = ((400, "llm_request_rejected"), (401, "llm_authentication"),
                 (403, "llm_permission"), (404, "llm_model_not_found"),
                 (408, "llm_timeout"), (422, "llm_request_rejected"),
                 (429, "llm_rate_limit"), (503, "llm_service"))
        for status, code in cases:
            with self.subTest(status=status):
                def fail(request):
                    return httpx.Response(status, json={"error": {
                        "message": "Authorization: Bearer test-key; secret-body; https://private.test/?token=hidden",
                    }})

                self.service.decisions.player = AiPlayer(provider=LlmProvider(transport=httpx.MockTransport(fail)))
                game = self.create()
                path = f"/api/games/{game['id']}"
                self.service.application.request_human_ai(game["id"], game["revision"])
                self.assertEqual(self.service.runner.advance_one(game["id"]).status, "paused")
                response = self.client.get(path)
                paused = response.json()
                self.assertEqual(paused["status"], "error_paused")
                self.assertFalse(paused["is_advancing"])
                self.assertEqual(paused["pause_error"]["code"], code)
                self.assertTrue(paused["pause_error"]["category"])
                self.assertTrue(paused["pause_error"]["suggestion"])
                for secret in ("test-key", "secret-body", "private.test", "traceback", "Authorization"):
                    self.assertNotIn(secret, response.text)
                self.assertEqual(self.client.get(path).json(), paused)
                self.assertEqual(self.client.get(path + "/export").json()["game"]["pause_error"], paused["pause_error"])
                self.assertEqual(paused["events"], game["events"])
                with GameUnitOfWork(self.service.database) as work:
                    self.assertNotIn("test-key", str(work.ai_decisions.list_decisions(game["id"])[-1].output))
                resumed = self.client.post(path + "/retry").json()
                self.assertIsNone(resumed["pause_error"])
                self.assertTrue(resumed["is_advancing"])

    def test_timeout_invalid_output_and_missing_configuration_are_distinct(self):
        def timeout(request):
            raise httpx.ReadTimeout("timeout with test-key", request=request)

        def invalid_output(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "not valid JSON"}}]})

        for handler, code in ((timeout, "llm_timeout"), (invalid_output, "llm_output_invalid")):
            with self.subTest(code=code):
                self.service.decisions.player = AiPlayer(provider=LlmProvider(transport=httpx.MockTransport(handler)))
                game = self.create()
                self.service.application.request_human_ai(game["id"], game["revision"])
                paused = drain(self.service, game["id"])
                self.assertEqual(paused["pause_error"]["code"], code)
                self.assertNotIn("test-key", json.dumps(paused))
        game = self.service.application.create_game(seed=2)
        self.service.application.request_human_ai(game["id"], game["revision"])
        self.assertEqual(drain(self.service, game["id"])["pause_error"]["code"], "llm_configuration")

    def test_internal_and_legacy_errors_never_expose_free_text(self):
        game = self.create()
        self.service.application.request_human_ai(game["id"], game["revision"])
        with patch.object(self.service.decisions, "decide", side_effect=RuntimeError("test-key internal secret")):
            paused = drain(self.service, game["id"])
        self.assertEqual(paused["pause_error"]["code"], "internal_error")
        self.assertNotIn("test-key", json.dumps(paused))
        for kind, public, private, code in (
            ("runner_error", {"error_type": "AuthenticationError", "error": "test-key"}, None, "llm_authentication"),
            ("ai_decision", {"validation_status": "error", "strategy_summary": "test-key"},
             {"error_type": "RateLimitError", "error_message": "test-key"}, "llm_rate_limit"),
        ):
            with self.subTest(kind=kind), GameUnitOfWork(self.service.database, write=True) as work:
                work.events.append_event(game["id"], kind, public, private)
            response = self.client.get(f"/api/games/{game['id']}")
            self.assertEqual(response.json()["pause_error"]["code"], code)
            self.assertNotIn("test-key", response.text)

    def test_http_errors_hide_exception_messages_and_tracebacks(self):
        with patch.object(self.service.queries, "get_game", side_effect=RuntimeError("Authorization test-key")):
            response = self.client.get("/api/games/unknown")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"]["code"], "internal_error")
        self.assertNotIn("test-key", response.text)
        self.assertNotIn("traceback", response.text)

    def test_legacy_error_audits_remain_safe_after_game_completes(self):
        game = self.create()
        self.service.application.request_human_ai(game["id"], game["revision"])
        with patch.object(self.service.decisions.player._provider, "chat_completion",
                          side_effect=TimeoutError("test-key legacy secret")):
            drain(self.service, game["id"])
        with GameUnitOfWork(self.service.database, write=True) as work:
            work.connection.execute(
                "update ai_decisions set strategy_summary=?, output=? where game_id=?",
                ("test-key legacy secret", json.dumps({"error_type": "TimeoutError", "error_message": "test-key"}),
                 game["id"]),
            )
            work.events.append_event(game["id"], "ai_decision",
                                     {"validation_status": "error", "strategy_summary": "test-key"},
                                     {"error_type": "TimeoutError", "error_message": "test-key"})
            work.events.append_event(game["id"], "runner_error", {"error": "test-key", "error_type": "TimeoutError"})
        self.service.application.resume_game(game["id"])
        play_to_completion(self.service, game["id"])
        path = f"/api/games/{game['id']}"
        for route in ("/room", "/events?include_private=true", "/export?include_private=true"):
            response = self.client.get(path + route)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("test-key", response.text)

    def test_human_ai_acceptance_returns_before_model_and_old_revision_cannot_repeat(self):
        game = self.create()
        path = f"/api/games/{game['id']}/ai-actions/human"
        response = self.client.post(path, json={"expected_revision": game["revision"]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_advancing"])
        self.assertEqual(response.json()["phase"], "team_proposal")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.client.post(path, json={"expected_revision": game["revision"]}).status_code, 409)
        updated = drain(self.service, game["id"])
        self.assertEqual(updated["next_human_action"], "speak")
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("ai_decision", self.client.get(f"/api/games/{game['id']}/events").text)

    def test_private_routes_are_409_until_complete_even_when_archived(self):
        game = self.create()
        path = f"/api/games/{game['id']}"
        for archived in (False, True):
            if archived: self.assertEqual(self.client.post(path + "/archive").status_code, 200)
            for route in ("/room", "/events?include_private=true", "/export?include_private=true"):
                self.assertEqual(self.client.get(path + route).status_code, 409)
            for route in ("", "/events", "/export"):
                self.assertEqual(self.client.get(path + route).status_code, 200)

    def test_completed_http_review_exports_and_real_provider_usage_contract(self):
        game = self.create()
        play_to_completion(self.service, game["id"])
        path = f"/api/games/{game['id']}"
        response = self.client.get(path + "/room")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertTrue(all(player["revealed_role"] for player in detail["game"]["players"]))
        self.assertEqual(detail["ai_decisions"][0]["total_tokens"], 100)
        self.assertEqual(detail["usage_by_model"][0]["average_cache_hit_rate"], 0.5)
        self.assertTrue(detail["ai_decisions"][0]["output_raw"])
        self.assertEqual(self.client.get(path + "/events?include_private=true").json(), detail["events"])
        self.assertTrue(any(event["private_payload"] for event in self.client.get(path + "/export?include_private=true").json()["events"]))
        self.assertNotIn("private_payload", self.client.get(path + "/export").text)
        public = self.client.get(path + "/events").json()
        self.assertEqual(len(public), len(detail["game"]["events"]))
        self.assertLess(len(public), len(detail["events"]))
        self.assertEqual(self.client.get(path + "/events?after=3").json(), public[3:])

    def test_legacy_vote_result_aggregates_votes_without_mutating_old_vote_frames(self):
        game = self.create()
        path = f"/api/games/{game['id']}"
        with GameUnitOfWork(self.service.database, write=True) as work:
            work.events.append_event(game["id"], "vote_cast", {"player_id": "player_2", "vote": "approve", "private_reason_summary": "secret"})
        before = self.client.get(path + "/events").json()
        self.assertEqual(before[-1]["public_payload"], {"player_id": "player_2"})
        cursor = before[-1]["event_index"]
        with GameUnitOfWork(self.service.database, write=True) as work:
            work.events.append_event(game["id"], "ai_decision", {"decision_type": "mission_action", "strategy_summary": "secret"})
            work.events.append_event(game["id"], "vote_result", {"approved": True, "failed_team_votes": 0})
        after = self.client.get(path + "/events").json()
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1]["public_payload"]["votes"], {"player_2": "approve"})
        self.assertEqual(after[-1]["event_index"], cursor + 1)
        self.assertEqual(self.client.get(path + f"/events?after={cursor}").json(), [after[-1]])

    def test_http_sse_uses_public_cursor_and_begins_with_state(self):
        game = self.create()
        self.service.application.request_human_ai(game["id"], game["revision"])
        drain(self.service, game["id"])
        events = self.api.list_events(game["id"])
        frames = asyncio.run(_http_sse_frames(self.app, game["id"], after=2, frame_count=1 + len(events[2:])))
        self.assertIn("event: game-state", frames[0])
        self.assertNotIn("id:", frames[0])
        self.assertTrue(all("event: game-event" in frame for frame in frames[1:]))
        data = [json.loads(frame.split("data: ", 1)[1].strip()) for frame in frames[1:]]
        self.assertEqual(data, events[2:])
        self.assertNotIn("ai_decision", "".join(frames))
        self.assertEqual(self.service.event_notifier.subscriber_count(game["id"]), 0)


class GamesEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_publishes_safe_pause_reason_without_private_audit(self):
        class FailingProvider:
            def chat_completion(self, profile, messages):
                raise TimeoutError("Authorization: secret-key")

        service = GameService(":memory:", ai_player=AiPlayer(provider=FailingProvider()))
        self.addCleanup(service.database.close)
        game = service.application.create_game(seed=2)
        service.application.request_human_ai(game["id"], game["revision"])
        with GameUnitOfWork(service.database) as work:
            profile = service.decisions.profile_for(work.load(game["id"])[0], work.summary(game["id"]), "player_1")
        # Supply a configured profile so this test exercises timeout rather than missing config.
        profile = profile.model_copy(update={"base_url": "https://example.test", "api_key": "secret-key"})
        with patch.object(service.decisions, "profile_for", return_value=profile):
            drain(service, game["id"])
        subscription = service.event_notifier.subscribe(game["id"])
        stream = game_event_stream_frames(GamesApi(service), game["id"], _OpenRequest(), subscription, after=2)
        frame = await stream.__anext__()
        self.assertIn('"code":"llm_timeout"', frame)
        self.assertNotIn("secret-key", frame)
        self.assertNotIn("ai_decision", frame)
        await stream.aclose()

    async def test_state_only_change_notifies_stream_without_advancing_public_cursor(self):
        service = GameService(":memory:")
        self.addCleanup(service.database.close)
        api = GamesApi(service)
        game = service.application.create_game(seed=2)
        subscription = service.event_notifier.subscribe(game["id"])
        stream = game_event_stream_frames(api, game["id"], _OpenRequest(), subscription, after=2, heartbeat_seconds=0.01)
        initial = await stream.__anext__()
        self.assertIn('"revision":1', initial)
        service.application.request_human_ai(game["id"], game["revision"])
        changed = await stream.__anext__()
        self.assertIn("event: game-state", changed)
        self.assertIn('"is_advancing":true', changed)
        self.assertNotIn("id:", changed)
        heartbeat = await stream.__anext__()
        self.assertEqual(heartbeat, sse_keep_alive_frame())
        await stream.aclose()
        self.assertEqual(service.event_notifier.subscriber_count(game["id"]), 0)

    async def test_sse_frame_contract(self):
        frame = sse_event_frame({"event_index": 7, "event_type": "speech", "public_payload": {"message": "你好"}})
        self.assertTrue(frame.startswith("id: 7\nevent: game-event\n"))
        self.assertIn('"message":"你好"', frame)


class _OpenRequest:
    async def is_disconnected(self): return False


async def _http_sse_frames(app, game_id, *, after, frame_count):
    frames = []
    done = asyncio.Event()
    sent_request = False
    status = []

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start": status.append(message["status"])
        if message["type"] == "http.response.body" and message.get("body"):
            frames.append(message["body"].decode())
            if len(frames) >= frame_count: done.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": f"/api/games/{game_id}/events/stream", "raw_path": b"/",
             "query_string": f"after={after}".encode(), "headers": [],
             "client": ("127.0.0.1", 1), "server": ("testserver", 80), "root_path": ""}
    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    assert status == [200], status
    return frames
