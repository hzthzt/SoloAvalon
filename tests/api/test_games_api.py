import json
import re
import tempfile
import unittest
from pathlib import Path

import backend.app.api.games as games_module
from backend.app.api.games import GamesApi
from backend.app.ai.player import AiDecisionError, AiPlayer
from backend.app.llm.provider import LlmCompletionResult
from backend.app.services.game_service import GameService
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.event_store import EventStore


class GamesApiTests(unittest.TestCase):
    def test_games_api_does_not_forward_seed_to_service(self):
        class RecordingService:
            def __init__(self):
                self.create_kwargs = None

            def create_game(self, **kwargs):
                self.create_kwargs = kwargs
                return {"id": "game_1"}

        service = RecordingService()
        api = GamesApi(service)

        api.create_game({"seed": 9, "human_name": "张三", "ai_names": ["A"]})

        self.assertEqual(service.create_kwargs, {
            "player_count": 5,
            "enabled_options": frozenset(),
            "human_name": "张三",
            "ai_names": ["A"],
            "default_llm_profile_id": None,
            "ai_profile_overrides": None,
        })

    def test_games_api_creates_and_returns_game_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)

            created = api.create_game({})
            loaded = api.get_game(created["id"])

            self.assertEqual(loaded["id"], created["id"])
            self.assertIn(loaded["human_player_id"], {player["id"] for player in loaded["players"]})

    def test_games_api_submits_action_and_exports_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            updated = api.submit_action(
                created["id"],
                {"action_type": "propose_team", "team": ["player_1", "player_2"]},
            )
            exported = api.export_game(created["id"])

            self.assertEqual(updated["next_human_action"], "speak")
            self.assertEqual(exported["game"]["id"], created["id"])

    def test_games_api_allows_ai_to_drive_current_human_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            updated = api.submit_human_ai_action(created["id"])

            self.assertEqual(updated["phase"], "speech")
            self.assertEqual(updated["next_human_action"], "speak")

    def test_games_api_retries_paused_game(self):
        class RetryService:
            def __init__(self):
                self.game_id = None

            def retry_paused_game(self, game_id):
                self.game_id = game_id
                return {"id": game_id, "status": "active"}

        service = RetryService()
        api = GamesApi(service)

        result = api.retry_paused_game("game_1")

        self.assertEqual(service.game_id, "game_1")
        self.assertEqual(result, {"id": "game_1", "status": "active"})

    def test_route_wrapper_reports_ai_timeout_as_gateway_timeout(self):
        def fail_with_timeout():
            raise _ai_decision_error("TimeoutError", "The read operation timed out")

        original_http_exception = games_module.HTTPException
        games_module.HTTPException = _FakeHTTPException
        try:
            with self.assertRaises(_FakeHTTPException) as captured:
                games_module._call(fail_with_timeout)
        finally:
            games_module.HTTPException = original_http_exception

        self.assertEqual(captured.exception.status_code, 504)
        self.assertEqual(captured.exception.detail["message"], "AI 决策失败：The read operation timed out")
        self.assertIn("AiDecisionError", captured.exception.detail["traceback"])
        self.assertIn("The read operation timed out", captured.exception.detail["traceback"])

    def test_route_wrapper_reports_value_errors_with_traceback(self):
        def fail_with_value_error():
            raise ValueError("bad payload")

        original_http_exception = games_module.HTTPException
        games_module.HTTPException = _FakeHTTPException
        try:
            with self.assertRaises(_FakeHTTPException) as captured:
                games_module._call(fail_with_value_error)
        finally:
            games_module.HTTPException = original_http_exception

        self.assertEqual(captured.exception.status_code, 400)
        self.assertEqual(captured.exception.detail["message"], "bad payload")
        self.assertEqual(captured.exception.detail["error_type"], "ValueError")
        self.assertIn("ValueError: bad payload", captured.exception.detail["traceback"])

    def test_route_wrapper_reports_unexpected_errors_with_traceback(self):
        def fail_with_unexpected_error():
            raise RuntimeError("database unavailable")

        original_http_exception = games_module.HTTPException
        games_module.HTTPException = _FakeHTTPException
        try:
            with self.assertRaises(_FakeHTTPException) as captured:
                games_module._call(fail_with_unexpected_error)
        finally:
            games_module.HTTPException = original_http_exception

        self.assertEqual(captured.exception.status_code, 500)
        self.assertEqual(captured.exception.detail["message"], "database unavailable")
        self.assertEqual(captured.exception.detail["error_type"], "RuntimeError")
        self.assertIn("RuntimeError: database unavailable", captured.exception.detail["traceback"])

    def test_list_events_hides_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            public_events = api.list_events(created["id"])

            self.assertNotIn("private_payload", public_events[1])

    def test_list_events_can_include_private_payload_explicitly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            private_events = api.list_events(created["id"], include_private=True)

            self.assertIn("private_payload", private_events[1])
            self.assertIn("roles_by_player_id", private_events[1]["private_payload"])

    def test_list_events_after_hides_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            public_events = api.list_events_after(created["id"], 1)

            self.assertTrue(all(event["event_index"] > 1 for event in public_events))
            self.assertTrue(all("private_payload" not in event for event in public_events))

    def test_sse_event_frame_contains_json_payload_and_event_id(self):
        frame = games_module.sse_event_frame(
            {
                "event_index": 7,
                "event_type": "speech",
                "public_payload": {"message": "你好"},
            }
        )

        self.assertTrue(frame.startswith("id: 7\n"))
        self.assertIn("event: game-event\n", frame)
        self.assertIn('"event_type":"speech"', frame)
        self.assertIn('"message":"你好"', frame)
        self.assertTrue(frame.endswith("\n\n"))

    def test_sse_keep_alive_frame_is_a_comment(self):
        self.assertEqual(games_module.sse_keep_alive_frame(), ": keep-alive\n\n")

    def test_public_events_hide_vote_values_until_vote_result_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(":memory:")
            initialize_database(connection)
            service = GameService(connection, ai_player=AiPlayer(provider=_DeterministicProvider()))
            api = GamesApi(service)
            event_store = EventStore(connection)
            state = service.create_game(seed=2)
            event_store.append_event(
                state["id"],
                "vote_cast",
                {"player_id": "player_2", "vote": "approve"},
            )

            in_progress_events = api.list_events(state["id"])
            in_progress_export = api.export_game(state["id"])
            hidden_vote_events = [
                event for event in in_progress_events if event["event_type"] == "vote_cast"
            ]
            hidden_export_votes = [
                event for event in in_progress_export["events"] if event["event_type"] == "vote_cast"
            ]

            self.assertGreater(len(hidden_vote_events), 0)
            self.assertTrue(
                all("vote" not in event["public_payload"] for event in hidden_vote_events)
            )
            self.assertTrue(
                all("vote" not in event["public_payload"] for event in hidden_export_votes)
            )

            event_store.append_event(
                state["id"],
                "vote_result",
                {"approved": True, "failed_team_votes": 0},
            )
            settled_events = api.list_events(state["id"])
            settled_vote_events = [
                event for event in settled_events if event["event_type"] == "vote_cast"
            ]

            self.assertTrue(
                all("vote" in event["public_payload"] for event in settled_vote_events)
            )

    def test_room_detail_includes_ai_decisions_and_usage_summaries(self):
        class UsageProvider(_DeterministicProvider):
            def chat_completion(self, profile, messages):
                return LlmCompletionResult(
                    content=super().chat_completion(profile, messages),
                    prompt_tokens=80,
                    completion_tokens=20,
                    total_tokens=100,
                    cached_tokens=40,
                    cache_hit_rate=0.5,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(":memory:")
            initialize_database(connection)
            api = GamesApi(GameService(connection, ai_player=AiPlayer(provider=UsageProvider())))
            created = api._service.create_game(seed=2)

            api.submit_human_ai_action(created["id"])
            detail = api.get_room_detail(created["id"])

            self.assertEqual(detail["game"]["id"], created["id"])
            self.assertGreater(len(detail["events"]), 0)
            self.assertGreater(len(detail["ai_decisions"]), 0)
            first_decision = detail["ai_decisions"][0]
            self.assertEqual(first_decision["total_tokens"], 100)
            self.assertEqual(first_decision["cache_hit_rate"], 0.5)
            self.assertTrue(
                any(summary["total_tokens"] >= 100 for summary in detail["usage_by_player"])
            )
            model_summary = next(
                summary for summary in detail["usage_by_model"] if summary["model_name"] == "unconfigured"
            )
            self.assertEqual(model_summary["average_cache_hit_rate"], 0.5)

    def test_games_api_archives_game_and_returns_archived_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)
            created = api._service.create_game(seed=2)

            archived = api.archive_game(created["id"])
            listed = api.list_games()[0]

            self.assertEqual(archived["id"], created["id"])
            self.assertIsNotNone(archived["archived_at"])
            self.assertEqual(listed["archived_at"], archived["archived_at"])


class GamesEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_waits_for_notification_then_sends_new_events(self):
        api = _StreamApi([
            [],
            [
                {
                    "event_index": 2,
                    "event_type": "speech",
                    "public_payload": {"message": "收到通知后补齐"},
                }
            ],
        ])
        subscription = _StreamSubscription([True])

        stream = games_module.game_event_stream_frames(
            api,
            "game_1",
            _OpenRequest(),
            subscription,
            after=1,
            heartbeat_seconds=0.01,
        )

        frame = await stream.__anext__()
        await stream.aclose()

        self.assertIn("event: game-event\n", frame)
        self.assertIn('"message":"收到通知后补齐"', frame)
        self.assertEqual(subscription.waited_timeouts, [0.01])
        self.assertTrue(subscription.closed)

    async def test_stream_sends_heartbeat_when_wait_times_out(self):
        api = _StreamApi([[]])
        subscription = _StreamSubscription([False])

        stream = games_module.game_event_stream_frames(
            api,
            "game_1",
            _OpenRequest(),
            subscription,
            after=0,
            heartbeat_seconds=0.01,
        )

        frame = await stream.__anext__()
        await stream.aclose()

        self.assertEqual(frame, ": keep-alive\n\n")
        self.assertTrue(subscription.closed)


def _api(tmpdir):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    return GamesApi(GameService(connection, ai_player=AiPlayer(provider=_DeterministicProvider())))


def _ai_decision_error(error_type, error_message):
    return AiDecisionError(
        error_type=error_type,
        error_message=error_message,
        input_summary="测试输入摘要",
        output_raw=None,
        output_parsed=None,
        prompt_template_name="speech",
        prompt_template_version="prompt.v1",
        context_builder_version="context-builder.v1",
        stable_prefix_hash="hash",
        context_summary="测试上下文摘要",
        context_truncated=False,
        prompt_messages=[],
    )


class _DeterministicProvider:
    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        if '"team"' in text:
            return json.dumps(
                {
                    "team": _player_ids(text)[:2],
                    "private_reason_summary": "测试模型选择合法车队。",
                    "public_message": "我先给一个方便观察票型的车队。",
                },
                ensure_ascii=False,
            )
        if "现在轮到你发言" in text:
            return json.dumps(
                {
                    "public_message": "我先围绕当前车队观察大家的表态。",
                    "private_reason_summary": "测试模型输出发言。",
                },
                ensure_ascii=False,
            )
        if "现在轮到你投票" in text:
            return json.dumps(
                {
                    "vote": "approve",
                    "private_reason_summary": "测试模型投赞成票。",
                },
                ensure_ascii=False,
            )
        if '"mission_action"' in text:
            return json.dumps(
                {
                    "mission_action": "success",
                    "private_reason_summary": "测试模型提交合法任务行动。",
                },
                ensure_ascii=False,
            )
        raise RuntimeError("unhandled test prompt")


class _FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _OpenRequest:
    async def is_disconnected(self):
        return False


class _StreamApi:
    def __init__(self, batches):
        self._batches = list(batches)

    def list_events_after(self, game_id, latest_event_index):
        del game_id, latest_event_index
        if self._batches:
            return self._batches.pop(0)
        return []


class _StreamSubscription:
    def __init__(self, wait_results):
        self._wait_results = list(wait_results)
        self.waited_timeouts = []
        self.closed = False

    async def wait(self, timeout):
        self.waited_timeouts.append(timeout)
        if self._wait_results:
            return self._wait_results.pop(0)
        return False

    def close(self):
        self.closed = True


def _player_ids(text):
    player_ids = []
    for player_id in re.findall(r"player_\d+", text):
        if player_id not in player_ids:
            player_ids.append(player_id)
    return player_ids
