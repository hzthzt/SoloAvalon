import json
import re
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

import backend.app.services.game_service as game_service_module
from backend.app.ai.player import AiDecisionError, AiPlayer, AiTurnResult
from backend.app.ai.strategy import SpeechDecision
from backend.app.game.models import Faction, GameOption, MissionAction, Phase, Role, Vote
from backend.app.game.rules import (
    assassinate,
    cast_vote,
    create_game as create_rules_game,
    finalize_quest,
    finalize_vote,
    propose_team,
    record_speech,
    submit_quest_action,
)
from backend.app.llm.provider import LlmCompletionResult
from backend.app.services.game_flow import GameReplayError
from backend.app.services.game_service import GameService
from backend.app.storage.ai_decision_repository import AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository
from backend.app.storage.database import connect_sqlite, initialize_database


class GameServiceTests(unittest.TestCase):
    def test_create_game_returns_human_filtered_state_and_logs_setup_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(seed=2)

            self.assertEqual(state["phase"], Phase.TEAM_PROPOSAL.value)
            self.assertEqual(state["human_player_id"], "player_1")
            visible_roles = {
                player["id"]: player["visible_role"]
                for player in state["players"]
            }
            revealed_roles = {
                player["id"]: player["revealed_role"]
                for player in state["players"]
            }
            self.assertEqual(visible_roles["player_1"], state["human_role"])
            self.assertEqual(set(revealed_roles.values()), {None})
            hidden_roles = [
                role for player_id, role in visible_roles.items() if player_id != "player_1"
            ]
            if state["human_role"] == Role.MERLIN.value:
                self.assertEqual(set(hidden_roles), {None, Role.UNKNOWN_EVIL.value})
            else:
                self.assertNotIn(Role.MERLIN.value, hidden_roles)

            events = service.list_events(state["id"])
            self.assertEqual(events[0].event_type, "game_created")
            self.assertEqual(events[1].event_type, "roles_assigned")

    def test_completed_game_state_reveals_all_roles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)
            internal_state = service._state(state["id"])
            for _round in range(3):
                internal_state = _complete_successful_quest(internal_state)
            assassin = next(player for player in internal_state.players if player.role == Role.ASSASSIN)
            merlin = next(player for player in internal_state.players if player.role == Role.MERLIN)
            internal_state = assassinate(internal_state, assassin.id, merlin.id)
            service._set_state(state["id"], internal_state)

            completed = service.get_game_state(state["id"])

            self.assertEqual(completed["phase"], Phase.COMPLETE.value)
            self.assertEqual(
                {
                    player["id"]: player["revealed_role"]
                    for player in completed["players"]
                },
                {
                    player.id: player.role.value
                    for player in internal_state.players
                },
            )

    def test_create_game_separates_internal_id_from_room_display_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            first = service.create_game(seed=2)
            second = service.create_game(seed=3)

            self.assertEqual(first["id"], "game_1")
            self.assertEqual(first["display_name"], "游戏#1")
            self.assertEqual(second["id"], "game_2")
            self.assertEqual(second["display_name"], "游戏#2")

    def test_create_game_returns_anonymous_names_and_keeps_original_names_out_of_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(
                seed=20260619,
                human_name="张三",
                ai_names=["阿尔法", "贝塔", "伽马", "德尔塔"],
            )

            self.assertEqual(
                [player["name"] for player in state["players"]],
                ["玩家1", "玩家2", "玩家3", "玩家4", "玩家5"],
            )
            human = next(player for player in state["players"] if player["is_human"])
            self.assertNotEqual(human["id"], "player_1")
            self.assertEqual(human["original_name"], "张三")
            self.assertCountEqual(
                [player["original_name"] for player in state["players"] if not player["is_human"]],
                ["阿尔法", "贝塔", "伽马", "德尔塔"],
            )

            ai_events = [
                event for event in service.list_events(state["id"]) if event.event_type == "ai_decision"
            ]
            self.assertGreater(len(ai_events), 0)
            prompt_text = "\n".join(
                message["content"]
                for event in ai_events
                for message in event.private_payload["prompt_messages"]
            )
            self.assertIn("玩家1", prompt_text)
            self.assertNotIn("张三", prompt_text)
            for original_name in ("阿尔法", "贝塔", "伽马", "德尔塔"):
                self.assertNotIn(original_name, prompt_text)

    def test_create_game_with_options_returns_rule_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(
                seed=9,
                player_count=8,
                enabled_options={GameOption.LADY_OF_LAKE, GameOption.ROLE_TIP_DETAIL},
            )

            self.assertEqual(state["player_count"], 8)
            self.assertEqual(len(state["players"]), 8)
            self.assertEqual(state["enabled_options"], ["lady_of_lake", "role_tip_detail"])
            self.assertEqual(state["missions"][0]["team_size"], 3)
            self.assertEqual(state["lady_of_lake_holder_player_id"], "player_8")
            self.assertEqual(state["lady_of_lake_previous_holder_ids"], ["player_8"])
            self.assertEqual(
                state["lady_of_lake_eligible_target_ids"],
                [f"player_{index}" for index in range(1, 8)],
            )
            self.assertEqual(state["lady_of_lake_known_factions"], {})

    def test_human_can_use_lady_of_lake_and_receive_private_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(
                seed=9,
                player_count=8,
                enabled_options={GameOption.LADY_OF_LAKE},
            )
            internal_state = create_rules_game(
                player_count=8,
                seed=9,
                human_seat_index=7,
                enabled_options={GameOption.LADY_OF_LAKE},
            )
            internal_state = _complete_successful_quest(internal_state)
            internal_state = _complete_successful_quest(internal_state)
            internal_state = replace(internal_state, leader_index=7)
            service._set_state(state["id"], internal_state)

            updated = service.submit_human_action(
                state["id"],
                "use_lady_of_lake",
                {"target_player_id": "player_1"},
            )

            self.assertEqual(updated["phase"], Phase.TEAM_PROPOSAL.value)
            self.assertEqual(updated["lady_of_lake_holder_player_id"], "player_1")
            self.assertEqual(
                updated["lady_of_lake_known_factions"],
                {"player_1": service._state(state["id"]).players[0].faction.value},
            )
            lake_events = [
                event for event in service.list_events(state["id"]) if event.event_type == "lady_of_lake_used"
            ]
            self.assertEqual(len(lake_events), 1)
            self.assertEqual(lake_events[0].public_payload["viewer_player_id"], "player_8")
            self.assertEqual(lake_events[0].public_payload["target_player_id"], "player_1")
            self.assertIn(lake_events[0].private_payload["target_faction"], {"good", "evil"})

    def test_human_actions_trigger_ai_until_next_human_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            self.assertEqual(state["phase"], Phase.SPEECH.value)
            self.assertEqual(state["next_human_action"], "speak")

            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )
            self.assertEqual(state["phase"], Phase.VOTING.value)
            self.assertEqual(state["next_human_action"], "vote")
            self.assertEqual(len(state["speeches"]), 5)

            state = service.submit_human_action(
                state["id"],
                "vote",
                {"vote": "approve"},
            )

            self.assertEqual(state["phase"], Phase.QUEST.value)
            self.assertEqual(state["next_human_action"], "mission_action")

    def test_public_state_and_events_normalize_legacy_player_ids_in_speeches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "我继续信任player_1和player_2。"},
            )

            speech_messages = list(state["speeches"].values())
            public_speech_events = [
                event for event in state["events"] if event["event_type"] == "speech"
            ]

            self.assertIn("我继续信任玩家1和玩家2。", speech_messages)
            self.assertTrue(
                all("player_1" not in message and "player_2" not in message for message in speech_messages)
            )
            self.assertTrue(
                any(
                    event["public_payload"]["message"] == "我继续信任玩家1和玩家2。"
                    for event in public_speech_events
                )
            )

    def test_ai_can_submit_current_human_decision_for_testing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            updated = service.submit_human_ai_action(state["id"])
            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])

            self.assertEqual(updated["phase"], Phase.SPEECH.value)
            self.assertEqual(updated["next_human_action"], "speak")
            self.assertIn("team_proposal", {decision.decision_type for decision in decisions})
            self.assertIn("player_1", {decision.player_id for decision in decisions})
            team_proposal_decision = next(
                decision for decision in decisions if decision.decision_type == "team_proposal"
            )
            self.assertEqual(team_proposal_decision.phase, Phase.TEAM_PROPOSAL.value)

    def test_ai_controlled_state_includes_public_event_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            updated = service.submit_human_ai_action(state["id"])

            event_types = {event["event_type"] for event in updated["events"]}
            self.assertIn("ai_decision", event_types)
            self.assertIn("team_proposed", event_types)
            self.assertTrue(
                all("private_payload" not in event for event in updated["events"])
            )

    def test_ai_turns_are_persisted_as_ai_decisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])

            self.assertGreaterEqual(len(decisions), 4)
            self.assertIn("speech", {decision.decision_type for decision in decisions})
            self.assertTrue(all(decision.prompt_template_version for decision in decisions))

    def test_auto_team_proposal_decision_keeps_team_proposal_phase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=20260619)

            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])
            team_decision = next(
                decision for decision in decisions if decision.decision_type == "team_proposal"
            )

            self.assertEqual(team_decision.phase, Phase.TEAM_PROPOSAL.value)

    def test_ai_turn_usage_is_persisted_as_ai_decision_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class UsageProvider(_DeterministicProvider):
                def chat_completion(self, profile, messages):
                    return LlmCompletionResult(
                        content=super().chat_completion(profile, messages),
                        prompt_tokens=90,
                        completion_tokens=30,
                        total_tokens=120,
                        cached_tokens=45,
                        cache_hit_rate=0.5,
                    )

            service = _service(tmpdir, ai_player=AiPlayer(provider=UsageProvider()))
            state = service.create_game(seed=2)

            service.submit_human_ai_action(state["id"])
            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])
            human_decision = next(
                decision for decision in decisions if decision.player_id == "player_1"
            )

            self.assertEqual(human_decision.prompt_tokens, 90)
            self.assertEqual(human_decision.completion_tokens, 30)
            self.assertEqual(human_decision.total_tokens, 120)
            self.assertEqual(human_decision.cached_tokens, 45)
            self.assertEqual(human_decision.cache_hit_rate, 0.5)

    def test_ai_decision_private_event_includes_prompt_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            service.submit_human_ai_action(state["id"])
            ai_events = [
                event for event in service.list_events(state["id"]) if event.event_type == "ai_decision"
            ]

            self.assertGreater(len(ai_events), 0)
            prompt_messages = ai_events[0].private_payload["prompt_messages"]
            roles = [message["role"] for message in prompt_messages]
            self.assertEqual(roles[0], "system")
            self.assertIn("user", roles)
            self.assertTrue(
                any("【你的视角】" in message["content"] for message in prompt_messages)
            )
            prompt_text = "\n".join(message["content"] for message in prompt_messages)
            self.assertIn("【本局配置】", prompt_text)
            self.assertIn("【活动日志】", prompt_text)
            self.assertIn("【本次行动】", prompt_text)
            self.assertNotIn("SoloAvalon", prompt_text)
            self.assertNotIn("隐藏真相", prompt_text)
            for schema_key in (
                "private_view",
                "public_state",
                "recent_public_events",
                "legal_actions",
            ):
                self.assertNotIn(schema_key, prompt_text)

    def test_ai_failure_is_logged_and_raised_without_synthetic_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class FailingProvider:
                def chat_completion(self, profile, messages):
                    raise RuntimeError("provider offline")

            service = _service(tmpdir, ai_player=AiPlayer(provider=FailingProvider()))
            state = service.create_game(seed=2)

            with self.assertRaises(AiDecisionError):
                service.submit_human_ai_action(state["id"])

            ai_events = [
                event for event in service.list_events(state["id"]) if event.event_type == "ai_decision"
            ]
            self.assertEqual(len(ai_events), 1)
            self.assertEqual(ai_events[0].public_payload["validation_status"], "error")
            self.assertIn("provider offline", ai_events[0].private_payload["error_message"])
            self.assertGreater(len(ai_events[0].private_payload["prompt_messages"]), 0)
            self.assertNotIn("team_proposed", {event.event_type for event in service.list_events(state["id"])})
            self.assertEqual(service.list_games()[0].status, "error_paused")

    def test_ai_retry_after_failure_restores_active_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class FailingProvider:
                def chat_completion(self, profile, messages):
                    raise RuntimeError("provider offline")

            service = _service(tmpdir, ai_player=AiPlayer(provider=FailingProvider()))
            state = service.create_game(seed=2)
            with self.assertRaises(AiDecisionError):
                service.submit_human_ai_action(state["id"])

            service._ai_player = AiPlayer(provider=_DeterministicProvider())
            updated = service.submit_human_ai_action(state["id"])

            self.assertEqual(updated["status"], "active")
            self.assertEqual(service.list_games()[0].status, "active")

    def test_retry_paused_game_continues_failed_non_human_ai_turn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class FailingProvider:
                def chat_completion(self, profile, messages):
                    raise RuntimeError("speaker provider offline")

            service = _service(tmpdir)
            state = service.create_game(seed=2)
            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )

            service._ai_player = AiPlayer(provider=FailingProvider())
            with self.assertRaises(AiDecisionError):
                service.submit_human_action(
                    state["id"],
                    "speak",
                    {"message": "I trust this opening team."},
                )
            self.assertEqual(service.list_games()[0].status, "error_paused")
            self.assertEqual(service.get_game_state(state["id"])["next_human_action"], None)

            service._ai_player = AiPlayer(provider=_DeterministicProvider())
            updated = service.retry_paused_game(state["id"])

            self.assertEqual(updated["status"], "active")
            self.assertEqual(updated["phase"], Phase.VOTING.value)
            self.assertEqual(updated["next_human_action"], "vote")
            self.assertEqual(service.list_games()[0].status, "active")

    def test_game_state_read_is_available_while_ai_advance_is_waiting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            started = threading.Event()
            release = threading.Event()

            class BlockingProvider(_DeterministicProvider):
                def chat_completion(self, profile, messages):
                    text = "\n".join(message["content"] for message in messages)
                    if "请求你执行 speak" in _current_action_text(text):
                        started.set()
                        release.wait(timeout=2)
                    return super().chat_completion(profile, messages)

            service = _service(tmpdir)
            state = service.create_game(seed=2)
            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            service._ai_player = AiPlayer(provider=BlockingProvider())

            worker = threading.Thread(
                target=lambda: service.submit_human_action(
                    state["id"],
                    "speak",
                    {"message": "I trust this opening team."},
                )
            )
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            try:
                before = time.monotonic()
                readable = service.get_game_state(state["id"])
                elapsed = time.monotonic() - before
            finally:
                release.set()
                worker.join(timeout=2)

            self.assertLess(elapsed, 0.5)
            self.assertEqual(readable["id"], state["id"])

    def test_ai_turns_are_persisted_as_private_memory_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            snapshots = AiMemoryRepository(service.connection).list_snapshots(state["id"])

            self.assertGreaterEqual(len(snapshots), 4)
            self.assertTrue(all("suspicions" in snapshot.memory_payload for snapshot in snapshots))
            self.assertTrue(all("key_observations" in snapshot.memory_payload for snapshot in snapshots))

    def test_good_quest_actions_are_submitted_without_llm_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class NoMissionPromptProvider(_DeterministicProvider):
                def chat_completion(self, profile, messages):
                    text = "\n".join(message["content"] for message in messages)
                    if "请求你执行 mission_action" in _current_action_text(text):
                        raise RuntimeError("good mission action should not call provider")
                    return super().chat_completion(profile, messages)

            service = _service(tmpdir, ai_player=AiPlayer(provider=NoMissionPromptProvider()))
            state = service.create_game(seed=2)
            internal_state = service._state(state["id"])
            good_ai = next(
                player
                for player in internal_state.players
                if not player.is_human and player.faction == Faction.GOOD
            )

            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", good_ai.id]},
            )
            state = service.submit_human_action(
                state["id"],
                "speak",
                {"message": "我先带一个好验证的队伍。"},
            )
            state = service.submit_human_action(
                state["id"],
                "vote",
                {"vote": "approve"},
            )
            state = service.submit_human_ai_action(state["id"])

            self.assertEqual(state["current_round"], 2)
            quest_submitters = [
                event.public_payload["player_id"]
                for event in service.list_events(state["id"])
                if event.event_type == "quest_action_submitted"
            ]
            self.assertIn("player_1", quest_submitters)
            self.assertIn(good_ai.id, quest_submitters)
            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])
            self.assertNotIn(
                ("mission_action", good_ai.id),
                {(decision.decision_type, decision.player_id) for decision in decisions},
            )
            self.assertNotIn(
                ("mission_action", "player_1"),
                {(decision.decision_type, decision.player_id) for decision in decisions},
            )

    def test_ai_turns_receive_public_event_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class RecordingAiPlayer:
                def __init__(self):
                    self.received_events = []

                def speak(self, state, player_id, profile, public_events=None):
                    self.received_events.append(public_events)
                    return AiTurnResult(
                        decision=SpeechDecision(
                            public_message=f"{player_id} has noted the history.",
                            stance="uncertain",
                            private_reason_summary="recorded public history",
                        ),
                        input_summary="recorded",
                        strategy_summary="recorded public history",
                        output_raw=None,
                        output_parsed=None,
                        validation_status="valid",
                        prompt_template_name="speech",
                        prompt_template_version="prompt.v1",
                        context_builder_version="context-builder.v1",
                        stable_prefix_hash="hash",
                        context_summary="recorded",
                        context_truncated=False,
                    )

            ai_player = RecordingAiPlayer()
            service = _service(tmpdir, ai_player=ai_player)
            state = service.create_game(seed=2)
            state = service.submit_human_action(
                state["id"],
                "propose_team",
                {"team": ["player_1", "player_2"]},
            )
            service.submit_human_action(
                state["id"],
                "speak",
                {"message": "I trust this opening team."},
            )

            self.assertGreater(len(ai_player.received_events), 0)
            first_history = ai_player.received_events[0]
            self.assertIsNotNone(first_history)
            self.assertIn("team_proposed", {event["event_type"] for event in first_history})
            self.assertTrue(all("private_payload" not in event for event in first_history))

    def test_default_ai_player_uses_live_provider_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(":memory:")
            initialize_database(connection)
            providers = []

            class RecordingAiPlayer:
                def __init__(self, provider=None):
                    providers.append(provider)

            original_ai_player = game_service_module.AiPlayer
            try:
                game_service_module.AiPlayer = RecordingAiPlayer
                GameService(connection)
            finally:
                game_service_module.AiPlayer = original_ai_player
                connection.close()

            self.assertEqual(providers, [None])

    def test_export_game_log_excludes_private_payload_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=6)

            public_export = service.export_game_log(state["id"])
            private_export = service.export_game_log(state["id"], include_private=True)

            self.assertNotIn("private_payload", public_export["events"][1])
            self.assertIn("private_payload", private_export["events"][1])

    def test_delete_game_removes_log_from_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=7)

            self.assertEqual(len(service.list_games()), 1)
            service.delete_game(state["id"])

            self.assertEqual(service.list_games(), [])
            with self.assertRaises(ValueError):
                service.get_game_state(state["id"])

    def test_archived_game_cannot_be_played_but_can_be_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=2)
            archived = service.archive_game(state["id"])

            self.assertIsNotNone(archived.archived_at)
            with self.assertRaisesRegex(ValueError, "archived game cannot be played"):
                service.submit_human_ai_action(state["id"])
            with self.assertRaisesRegex(ValueError, "archived game cannot be played"):
                service.submit_human_action(
                    state["id"],
                    "propose_team",
                    {"team": ["player_1", "player_2"]},
                )

            self.assertEqual(service.get_game_state(state["id"])["id"], state["id"])
            self.assertGreater(len(service.list_events(state["id"])), 0)
            self.assertEqual(service.get_room_detail(state["id"])["game"]["id"], state["id"])
            self.assertEqual(service.export_game_log(state["id"])["game"]["id"], state["id"])

    def test_active_game_state_can_be_restored_from_persisted_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(database_path)
            try:
                initialize_database(connection)
                service = GameService(connection, ai_player=AiPlayer(provider=_DeterministicProvider()))
                state = service.create_game(seed=2)
                state = service.submit_human_action(
                    state["id"],
                    "propose_team",
                    {"team": ["player_1", "player_2"]},
                )
                state = service.submit_human_action(
                    state["id"],
                    "speak",
                    {"message": "I trust this opening team."},
                )
                state = service.submit_human_action(
                    state["id"],
                    "vote",
                    {"vote": "approve"},
                )
            finally:
                connection.close()

            restored_connection = connect_sqlite(database_path)
            try:
                initialize_database(restored_connection)
                restored_service = GameService(restored_connection)

                restored = restored_service.get_game_state(state["id"])

                self.assertEqual(restored["phase"], state["phase"])
                self.assertEqual(restored["next_human_action"], "mission_action")
                self.assertEqual(restored["speeches"], state["speeches"])
                self.assertEqual(
                    restored["quest_actions_submitted_count"],
                    state["quest_actions_submitted_count"],
                )
            finally:
                restored_connection.close()

    def test_restore_reports_stale_invalid_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(database_path)
            try:
                initialize_database(connection)
                service = GameService(connection, ai_player=AiPlayer(provider=_DeterministicProvider()))
                state = service.create_game(seed=2)
                service._events.append_event(
                    state["id"],
                    "quest_action_submitted",
                    {"player_id": "player_1"},
                    {"mission_action": MissionAction.SUCCESS.value},
                )
                event_count = len(service.list_events(state["id"]))
            finally:
                connection.close()

            restored_connection = connect_sqlite(database_path)
            try:
                initialize_database(restored_connection)
                restored_service = GameService(restored_connection)

                with self.assertRaises(GameReplayError) as raised:
                    restored_service.get_game_state(state["id"])

                self.assertEqual(raised.exception.last_replayed_event_index, 0)
                self.assertEqual(raised.exception.failed_event_index, event_count)
                self.assertIn(
                    "quest actions can only be submitted during quest phase",
                    str(raised.exception),
                )
            finally:
                restored_connection.close()

    def test_auto_advance_persists_progress_before_later_ai_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(
                tmpdir,
                ai_player=AiPlayer(provider=_TimeoutTeamProposalProvider()),
            )
            state = service.create_game(
                seed=2,
                player_count=10,
                enabled_options={GameOption.LADY_OF_LAKE},
            )
            internal_state = service._state(state["id"])
            internal_state = replace(
                internal_state,
                phase=Phase.QUEST,
                current_round=2,
                leader_index=1,
                proposed_team=("player_2", "player_3", "player_4", "player_5"),
                quest_results=(True,),
                quest_actions={},
            )
            service._set_state(state["id"], internal_state)

            with self.assertRaises(AiDecisionError):
                service._auto_advance(state["id"])
            quest_actions_after_error = [
                event
                for event in service.list_events(state["id"])
                if event.event_type == "quest_action_submitted"
            ]

            with self.assertRaises(AiDecisionError):
                service._auto_advance(state["id"])

            self.assertEqual(
                [
                    event.event_index
                    for event in service.list_events(state["id"])
                    if event.event_type == "quest_action_submitted"
                ],
                [event.event_index for event in quest_actions_after_error],
            )
            self.assertEqual(service._state(state["id"]).phase, Phase.TEAM_PROPOSAL)


def _service(tmpdir, ai_player=None):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    if ai_player is None:
        ai_player = AiPlayer(provider=_DeterministicProvider())
    return GameService(connection, ai_player=ai_player)


def _complete_successful_quest(state):
    team_size = state.missions[state.current_round - 1].team_size
    team = state.players[:team_size]
    leader = state.players[state.leader_index]
    state = propose_team(state, leader.id, tuple(player.id for player in team))
    for player_id in state.speech_order:
        state = record_speech(state, player_id, f"{player_id} supports the service test team")
    for player in state.players:
        state = cast_vote(state, player.id, Vote.APPROVE)
    state = finalize_vote(state)
    for player in team:
        state = submit_quest_action(state, player.id, MissionAction.SUCCESS)
    return finalize_quest(state)


class _DeterministicProvider:
    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        action_text = _current_action_text(text)
        if "请求你执行 propose_team" in action_text:
            team_size = _team_size(action_text)
            return json.dumps(
                {
                    "team": _player_ids(action_text)[:team_size],
                    "private_reason_summary": "测试模型选择合法车队。",
                    "public_message": "我先给一个方便观察票型的车队。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 speak" in action_text:
            return json.dumps(
                {
                    "public_message": "我先围绕当前车队观察大家的表态，投票分布会很有价值。",
                    "private_reason_summary": "测试模型输出发言。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 vote" in action_text:
            return json.dumps(
                {
                    "vote": "approve",
                    "private_reason_summary": "测试模型投赞成票。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 mission_action" in action_text:
            return json.dumps(
                {
                    "mission_action": "success",
                    "private_reason_summary": "测试模型提交合法任务行动。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 assassinate" in action_text or "请求你执行 use_lady_of_lake" in action_text:
            viewer = _viewer_id(text)
            candidates = [player_id for player_id in _player_ids(action_text) if player_id != viewer]
            return json.dumps(
                {
                    "target_player_id": candidates[0],
                    "private_reason_summary": "测试模型选择合法刺杀目标。",
                    "candidate_ranking": candidates,
                },
                ensure_ascii=False,
            )
        raise RuntimeError("unhandled test prompt")


class _TimeoutTeamProposalProvider(_DeterministicProvider):
    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        action_text = _current_action_text(text)
        if "请求你执行 propose_team" in action_text:
            raise TimeoutError("测试组队超时")
        return super().chat_completion(profile, messages)


def _current_action_text(text):
    return text.split("【本次行动】", 1)[-1]


def _team_size(text):
    match = re.search(r"车队人数：(\d+)", text)
    return int(match.group(1)) if match else 2


def _player_ids(text):
    player_ids = []
    for player_id in re.findall(r"player_\d+", text):
        if player_id not in player_ids:
            player_ids.append(player_id)
    return player_ids


def _viewer_id(text):
    match = re.search(r"你是 (player_\d+)", text)
    return match.group(1) if match else ""
