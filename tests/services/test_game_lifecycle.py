from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.ai.player import AiPlayer
from backend.app.main import create_app
from tests.game_support import _DeterministicProvider, make_service, wait_until


class GameLifecycleTests(unittest.TestCase):
    def test_lifespan_recovers_persisted_human_once_and_stops_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.sqlite"
            original = make_service(path)
            game = original.application.create_game(seed=2)
            original.application.request_human_ai(game["id"], game["revision"])
            original.database.close()
            app = create_app(path, ai_player=AiPlayer(provider=_DeterministicProvider()), scan_seconds=0.02)
            with TestClient(app) as client:
                response = wait_until(lambda: client.get(f"/api/games/{game['id']}").json(), lambda data: not data["is_advancing"])
                self.assertEqual(response["next_human_action"], "speak")
                self.assertEqual(len([event for event in response["events"] if event["event_type"] == "team_proposed"]), 1)
            self.assertIsNone(app.state.services.runner._scheduler)

    def test_periodic_scan_recovers_a_commit_without_wakeup(self):
        service = make_service(scan_seconds=0.02)
        try:
            service.runner.start()
            service.application.wake = lambda: None
            game = service.application.create_game(seed=2)
            service.application.request_human_ai(game["id"], game["revision"])
            updated = wait_until(lambda: service.queries.get_game(game["id"]), lambda data: not data["is_advancing"])
            self.assertEqual(updated["phase"], "speech")
        finally:
            service.runner.stop()
            service.database.close()

    def test_executor_is_bounded_and_shutdown_preserves_uncommitted_intents(self):
        class BlockingProvider(_DeterministicProvider):
            def __init__(self):
                self.release = threading.Event()
                self.lock = threading.Lock()
                self.calls = 0

            def chat_completion(self, profile, messages):
                with self.lock: self.calls += 1
                if not self.release.wait(5): raise AssertionError("test provider timed out")
                return super().chat_completion(profile, messages)

        provider = BlockingProvider()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.sqlite"
            service = make_service(path, provider, max_workers=2, scan_seconds=0.02)
            games = [service.application.create_game(seed=2) for _ in range(3)]
            for game in games: service.application.request_human_ai(game["id"], game["revision"])
            try:
                service.runner.start()
                wait_until(lambda: provider.calls, lambda count: count == 2)
                self.assertEqual(len(service.runner._running), 2)
                service.runner.stop()
                self.assertEqual(provider.calls, 2)
            finally:
                provider.release.set()
                service.runner.stop()
                wait_until(lambda: len(service.runner._running), lambda count: count == 0)
                service.database.close()
            restored = make_service(path)
            try:
                for game in games:
                    state = restored.queries.get_game(game["id"])
                    self.assertTrue(state["is_advancing"])
                    self.assertEqual(state["phase"], "team_proposal")
                    self.assertEqual(state["revision"], game["revision"] + 1)
            finally:
                restored.database.close()

    def test_app_factories_have_isolated_memory_databases(self):
        apps = [create_app(":memory:", ai_player=AiPlayer(provider=_DeterministicProvider())) for _ in range(2)]
        with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
            self.assertEqual(first.post("/api/games", json={}).status_code, 200)
            self.assertEqual(second.get("/api/games").json(), [])
