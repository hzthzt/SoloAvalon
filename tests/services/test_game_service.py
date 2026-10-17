from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.ai.player import AiPlayer
from backend.app.game.models import MissionConfig, Phase
from backend.app.services.game_flow import GameReplayError, GameStateLoader
from backend.app.services.game_work import GameConflict, GameUnitOfWork
from backend.app.services.game_runner import GameAdvanceError
from backend.app.storage.event_store import EventStore
from backend.app.storage.game_repository import GameRepository
from tests.game_support import (
    _DeterministicProvider, make_service, drain, play_to_completion, wait_until,
)


class GameServiceTests(unittest.TestCase):
    def service(self, database=":memory:", provider=None, **options):
        service = make_service(database, provider, **options)
        self.addCleanup(service.database.close)
        self.addCleanup(service.runner.stop)
        return service

    def test_creation_is_public_and_does_not_call_ai(self):
        service = self.service(provider=_BlockingProvider())
        game = service.application.create_game(seed=1)
        self.assertEqual(game["revision"], 1)
        self.assertEqual(len(game["events"]), 2)
        self.assertNotIn("role_set", service.queries.list_games()[0])
        self.assertTrue(all(player["revealed_role"] is None for player in game["players"]))
        self.assertEqual(game, service.queries.get_game(game["id"]))

    def test_create_rolls_back_at_each_setup_event(self):
        for fail_at in range(1, 8):
            with self.subTest(fail_at=fail_at):
                service = self.service()
                original = EventStore.append_event
                calls = []

                def fail(store, *args, **kwargs):
                    calls.append(args)
                    if len(calls) == fail_at:
                        raise RuntimeError("setup failure")
                    return original(store, *args, **kwargs)

                with patch.object(EventStore, "append_event", fail):
                    with self.assertRaisesRegex(RuntimeError, "setup failure"):
                        service.application.create_game(seed=2)
                with GameUnitOfWork(service.database) as work:
                    for table in ("games", "players", "game_events"):
                        self.assertEqual(work.connection.execute(f"select count(*) from {table}").fetchone()[0], 0)

    def test_player_insertion_failure_leaves_no_partial_game(self):
        service = self.service()
        with GameUnitOfWork(service.database, write=True) as work:
            work.connection.execute("""create trigger fail_player before insert on players
                when new.seat_index = 1 begin select raise(abort, 'player failure'); end""")
        with self.assertRaisesRegex(Exception, "player failure"):
            service.application.create_game(seed=2)
        self.assertEqual(service.queries.list_games(), [])

    def test_human_step_and_revision_are_one_transaction(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        with patch.object(GameRepository, "update_runtime", side_effect=RuntimeError("runtime failure")):
            with self.assertRaisesRegex(RuntimeError, "runtime failure"):
                service.application.submit_human_action(game["id"], game["revision"], "propose_team", {"team": ["player_1", "player_2"]})
        self.assertEqual(service.queries.get_game(game["id"]), game)

    def test_duplicate_revision_is_accepted_only_once(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        barrier = threading.Barrier(2)
        results = []

        def submit():
            barrier.wait()
            try:
                service.application.submit_human_action(game["id"], game["revision"], "propose_team", {"team": ["player_1", "player_2"]})
                results.append("accepted")
            except GameConflict as error:
                results.append(error.current_revision)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertCountEqual(results, ["accepted", game["revision"] + 1])

    def test_human_ai_only_records_intent_then_executes_once(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        accepted = service.application.request_human_ai(game["id"], game["revision"])
        self.assertEqual(accepted["phase"], "team_proposal")
        self.assertTrue(accepted["is_advancing"])
        self.assertEqual(len(accepted["events"]), len(game["events"]))
        result = drain(service, game["id"])
        self.assertEqual(result["next_human_action"], "speak")
        self.assertFalse(result["is_advancing"])
        self.assertEqual(len([event for event in result["events"] if event["event_type"] == "team_proposed"]), 1)
        with GameUnitOfWork(service.database) as work:
            self.assertEqual(len(work.ai_decisions.list_decisions(game["id"])), 1)
            self.assertEqual(len(work.ai_memory.list_snapshots(game["id"], game["human_player_id"])), 1)

    def test_slow_model_holds_no_database_transaction_and_other_rooms_work(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = _BlockingProvider()
            service = self.service(Path(directory) / "games.sqlite", provider)
            game = service.application.create_game(seed=2)
            service.application.request_human_ai(game["id"], game["revision"])
            thread = threading.Thread(target=service.runner.advance_one, args=(game["id"],))
            thread.start()
            try:
                self.assertTrue(provider.entered.wait(3))
                self.assertEqual(service.runner.advance_one(game["id"]).status, "busy")
                self.assertTrue(service.queries.get_game(game["id"])["is_advancing"])
                other = service.application.create_game(seed=2)
                updated = service.application.submit_human_action(other["id"], other["revision"], "propose_team", {"team": ["player_1", "player_2"]})
                self.assertEqual(updated["phase"], "speech")
                with GameUnitOfWork(service.database, write=True) as work:
                    self.assertTrue(work.connection.in_transaction)
            finally:
                provider.release.set()
                thread.join(3)
                service.runner.stop()
                service.database.close()
            self.assertFalse(thread.is_alive())

    def test_stale_success_and_failure_do_not_change_new_state(self):
        for failure in (False, True):
            for mutation in ("manual", "archive", "resume", "delete"):
                with self.subTest(failure=failure, mutation=mutation):
                    provider = _BlockingProvider(failure=failure)
                    service = self.service(provider=provider)
                    game = service.application.create_game(seed=2)
                    accepted = service.application.request_human_ai(game["id"], game["revision"])
                    outcomes = []
                    thread = threading.Thread(target=lambda: outcomes.append(service.runner.advance_one(game["id"]).status))
                    thread.start()
                    try:
                        self.assertTrue(provider.entered.wait(3))
                        if mutation == "manual":
                            service.application.submit_human_action(game["id"], accepted["revision"], "propose_team", {"team": ["player_1", "player_2"]})
                        elif mutation == "archive":
                            service.application.archive_game(game["id"])
                        elif mutation == "resume":
                            service.application.resume_game(game["id"])
                        else:
                            service.application.delete_game(game["id"])
                            # Reusing an old readable id must not accept the old answer either.
                            service.application.create_game(seed=2)
                        before = service.queries.get_game(game["id"])
                    finally:
                        provider.release.set()
                        thread.join(3)
                    self.assertEqual(outcomes, ["discarded"])
                    self.assertEqual(service.queries.get_game(game["id"]), before)
                    with GameUnitOfWork(service.database) as work:
                        self.assertEqual(work.ai_decisions.list_decisions(game["id"]), [])

    def test_every_ai_stage_pauses_atomically_and_can_resume(self):
        for action in ("propose_team", "speak", "vote", "mission_action", "assassinate", "use_lady_of_lake"):
            with self.subTest(action=action):
                service = self.service(provider=_FailActionProvider(action))
                game = service.application.create_game(seed=2, player_count=8, enabled_options={"lady_of_lake"})
                for _ in range(120):
                    game = drain(service, game["id"])
                    if game["status"] == "error_paused": break
                    self.assertNotEqual(game["phase"], "complete", f"{action} was never called")
                    service.application.request_human_ai(game["id"], game["revision"])
                self.assertEqual(game["status"], "error_paused")
                self.assertFalse(game["is_advancing"])
                with GameUnitOfWork(service.database) as work:
                    paused_mode = work.summary(game["id"]).advance_mode
                    decisions = work.ai_decisions.list_decisions(game["id"])
                    self.assertEqual(decisions[-1].validation_status, "error")
                    self.assertIsNotNone(paused_mode)
                service.decisions.player = AiPlayer(provider=_DeterministicProvider())
                resumed = service.application.resume_game(game["id"])
                self.assertTrue(resumed["is_advancing"])
                self.assertIn(drain(service, game["id"])["status"], {"active", "complete"})

    def test_late_executor_error_does_not_pause_a_new_execution(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        service.application.request_human_ai(game["id"], game["revision"])
        with GameUnitOfWork(service.database) as work:
            old = work.summary(game["id"])
        current = service.application.resume_game(game["id"])
        with patch.object(service.runner, "advance_one", side_effect=GameAdvanceError(old, RuntimeError("old write failed"))):
            with self.assertLogs("backend.app.services.game_runner", level="ERROR"):
                service.runner._run(game["id"])
        self.assertEqual(service.queries.get_game(game["id"]), current)

    def test_human_once_and_error_pause_survive_database_reopen(self):
        for paused in (False, True):
            with self.subTest(paused=paused), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "games.sqlite"
                first = self.service(path, _FailActionProvider("propose_team") if paused else None)
                game = first.application.create_game(seed=2)
                first.application.request_human_ai(game["id"], game["revision"])
                if paused:
                    self.assertEqual(first.runner.advance_one(game["id"]).status, "paused")
                first.database.close()
                second = self.service(path)
                second.runner.recover()
                if paused:
                    self.assertEqual(second.runner.advance_one(game["id"]).status, "blocked")
                    second.application.resume_game(game["id"])
                result = drain(second, game["id"])
                self.assertEqual(result["next_human_action"], "speak")
                self.assertFalse(result["is_advancing"])
                self.assertEqual(len([event for event in result["events"] if event["event_type"] == "team_proposed"]), 1)
                second.database.close()

    def test_startup_recovers_legacy_ai_turn_but_not_human_or_paused_or_archived(self):
        service = self.service()
        ids = []
        for mode in ("ai", "human", "paused", "archived", "complete"):
            game = service.application.create_game(seed=2 if mode == "human" else 1)
            ids.append(game["id"])
            with GameUnitOfWork(service.database, write=True) as work:
                work.connection.execute("update games set advance_mode = null, revision = 0 where id = ?", (game["id"],))
                if mode == "paused": work.games.update_game_status(game["id"], "error_paused")
                if mode == "archived": work.games.archive_game(game["id"])
                if mode == "complete": work.games.update_game_status(game["id"], "complete")
        service.runner.recover()
        with GameUnitOfWork(service.database) as work:
            self.assertEqual([work.summary(game_id).advance_mode for game_id in ids], ["auto", None, None, None, None])
        service.runner.recover()
        game = drain(service, ids[0])
        count = len(game["events"])
        service.application.resume_game(ids[0])
        self.assertEqual(len(drain(service, ids[0])["events"]), count)

    def test_reads_never_resume_or_change_revision(self):
        service = self.service()
        game = service.application.create_game(seed=1)
        for _ in range(2):
            self.assertEqual(service.queries.get_game(game["id"]), game)
            service.queries.get_public_events(game["id"])
            service.queries.export_game(game["id"])
            service.queries.list_games()
        self.assertEqual(service.queries.get_game(game["id"])["revision"], game["revision"])

    def test_unknown_or_damaged_events_stop_recovery_without_truncation(self):
        for kind, payload, private in (("future_rule", {}, None), ("quest_action_submitted", {"player_id": "player_1"}, None)):
            with self.subTest(kind=kind):
                service = self.service()
                game = service.application.create_game(seed=1)
                with GameUnitOfWork(service.database, write=True) as work:
                    bad = work.events.append_event(game["id"], kind, payload, private)
                with self.assertLogs("backend.app.services.game_runner", level="ERROR"):
                    service.runner.recover()
                with self.assertRaises(GameReplayError) as captured:
                    service.application.resume_game(game["id"])
                self.assertEqual(captured.exception.failed_event_index, bad.event_index)
                with GameUnitOfWork(service.database) as work:
                    self.assertEqual(work.summary(game["id"]).status, "error_paused")
                    self.assertEqual(work.events.list_events(game["id"])[-1].event_index, bad.event_index)

    def test_saved_missions_take_precedence_and_legacy_missing_missions_falls_back(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        replacement = tuple(MissionConfig(round_number=n + 1, team_size=3, fail_cards_required=1) for n in range(5))
        with patch("backend.app.services.game_flow.STANDARD_MISSION_CONFIGS", {5: replacement}):
            self.assertEqual(service.queries.get_game(game["id"])["missions"], game["missions"])
            with GameUnitOfWork(service.database, write=True) as work:
                event = work.events.list_events(game["id"])[0]
                payload = dict(event.public_payload)
                del payload["missions"]
                work.connection.execute("update game_events set public_payload = ? where id = ?", (json.dumps(payload), event.id))
            self.assertEqual(service.queries.get_game(game["id"])["missions"][0]["team_size"], 3)

    def test_summary_rebuild_preserves_runtime_and_configuration(self):
        service = self.service()
        game = service.application.create_game(seed=2, default_llm_profile_id="retained")
        with GameUnitOfWork(service.database, write=True) as work:
            work.games.update_runtime(game["id"], advance_mode="human_once", status="error_paused")
            work.games.archive_game(game["id"])
            before = work.summary(game["id"])
            work.connection.execute("update games set current_round=5, current_phase='complete', winner='evil' where id=?", (game["id"],))
        service.runner.recover()
        with GameUnitOfWork(service.database) as work:
            after = work.summary(game["id"])
            self.assertEqual(after.current_round, 1)
            self.assertEqual(after.current_phase, "team_proposal")
            self.assertIsNone(after.winner)
            for field in ("status", "revision", "advance_mode", "archived_at", "default_llm_profile_id"):
                self.assertEqual(getattr(after, field), getattr(before, field))

    def test_archive_cancels_intent_and_step_limit_is_diagnostic_pause(self):
        service = self.service(max_steps=1)
        game = service.application.create_game(seed=1)
        service.runner._run(game["id"])
        paused = service.queries.get_game(game["id"])
        self.assertEqual(paused["status"], "error_paused")
        with GameUnitOfWork(service.database) as work:
            self.assertEqual(work.events.list_events(game["id"])[-1].public_payload["error"], "advance step limit exceeded")
        service.application.archive_game(game["id"])
        with GameUnitOfWork(service.database) as work:
            self.assertIsNone(work.summary(game["id"]).advance_mode)
        self.assertEqual(service.runner.advance_one(game["id"]).status, "blocked")

    def test_completed_review_has_roles_audit_and_usage_but_public_history_stays_public(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        with self.assertRaises(GameConflict): service.queries.get_completed_review(game["id"])
        completed = play_to_completion(service, game["id"])
        self.assertTrue(all(player["revealed_role"] for player in completed["players"]))
        review = service.queries.get_completed_review(game["id"])
        self.assertTrue(review["ai_decisions"])
        self.assertTrue(review["usage_by_player"])
        self.assertTrue(any(event["private_payload"] for event in review["events"]))
        public = service.queries.get_public_events(game["id"])
        self.assertTrue(all(event["event_type"] != "ai_decision" for event in public))
        self.assertEqual([event["event_index"] for event in public], list(range(1, len(public) + 1)))
        self.assertTrue(all("vote" not in event["public_payload"] for event in public if event["event_type"] == "vote_cast"))
        self.assertTrue(all(len(event["public_payload"]["votes"]) == 5 for event in public if event["event_type"] == "vote_result"))

    def test_good_quest_action_does_not_call_model(self):
        service = self.service()
        game = service.application.create_game(seed=2)
        play_to_completion(service, game["id"])
        with GameUnitOfWork(service.database) as work:
            players = {player.id: player for player in work.games.list_players(game["id"])}
            for decision in work.ai_decisions.list_decisions(game["id"]):
                if decision.decision_type == "mission_action":
                    self.assertEqual(players[decision.player_id].faction, "evil")

    def test_all_supported_player_counts_and_options_complete(self):
        for count in range(5, 11):
            with self.subTest(count=count):
                service = self.service()
                options = {"role_tip_detail"}
                if count >= 8: options.add("lady_of_lake")
                if count >= 9: options.add("tristan_isolde")
                game = service.application.create_game(seed=2, player_count=count, enabled_options=options)
                self.assertEqual(play_to_completion(service, game["id"])["status"], "complete")


class _BlockingProvider(_DeterministicProvider):
    def __init__(self, failure=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failure = failure

    def chat_completion(self, profile, messages):
        self.entered.set()
        if not self.release.wait(5): raise AssertionError("blocking test timed out")
        if self.failure: raise TimeoutError("old model timeout")
        return super().chat_completion(profile, messages)


class _FailActionProvider(_DeterministicProvider):
    def __init__(self, action):
        self.action = action

    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages).split("【本次行动】", 1)[-1]
        if f"请求你执行 {self.action}" in text:
            raise TimeoutError("stage timeout")
        return super().chat_completion(profile, messages)
