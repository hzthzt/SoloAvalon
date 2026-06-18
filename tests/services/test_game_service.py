import json
import re
import tempfile
import unittest
from pathlib import Path

import backend.app.services.game_service as game_service_module
from backend.app.ai.player import AiDecisionError, AiPlayer, AiTurnResult
from backend.app.ai.strategy import SpeechDecision
from backend.app.game.models import Faction, Phase, Role
from backend.app.services.game_service import GameService
from backend.app.storage.ai_decision_repository import AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository
from backend.app.storage.database import connect_sqlite, initialize_database


class GameServiceTests(unittest.TestCase):
    def test_create_game_returns_human_filtered_state_and_logs_setup_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(seed=20260615)

            self.assertEqual(state["phase"], Phase.TEAM_PROPOSAL.value)
            self.assertEqual(state["human_player_id"], "player_1")
            visible_roles = {
                player["id"]: player["visible_role"]
                for player in state["players"]
            }
            self.assertEqual(visible_roles["player_1"], state["human_role"])
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

    def test_create_game_uses_timestamp_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)

            state = service.create_game(seed=20260615)

            self.assertRegex(state["id"], r"^\d{8}_\d{6}_\d{6}$")

    def test_human_actions_trigger_ai_until_next_human_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

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

    def test_ai_can_submit_current_human_decision_for_testing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            updated = service.submit_human_ai_action(state["id"])
            decisions = AiDecisionRepository(service.connection).list_decisions(state["id"])

            self.assertEqual(updated["phase"], Phase.SPEECH.value)
            self.assertEqual(updated["next_human_action"], "speak")
            self.assertIn("team_proposal", {decision.decision_type for decision in decisions})
            self.assertIn("player_1", {decision.player_id for decision in decisions})

    def test_ai_controlled_state_includes_public_event_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

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
            state = service.create_game(seed=1)

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

    def test_ai_decision_private_event_includes_prompt_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

            service.submit_human_ai_action(state["id"])
            ai_events = [
                event for event in service.list_events(state["id"]) if event.event_type == "ai_decision"
            ]

            self.assertGreater(len(ai_events), 0)
            prompt_messages = ai_events[0].private_payload["prompt_messages"]
            roles = [message["role"] for message in prompt_messages]
            self.assertEqual(roles[0], "system")
            self.assertIn("user", roles)
            self.assertIn("Prompt", prompt_messages[0]["content"])
            self.assertTrue(
                any("【你的视角】" in message["content"] for message in prompt_messages)
            )
            prompt_text = "\n".join(message["content"] for message in prompt_messages)
            self.assertIn("【本局配置】", prompt_text)
            self.assertIn("【公开记录】", prompt_text)
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
            state = service.create_game(seed=1)

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

    def test_ai_turns_are_persisted_as_private_memory_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = _service(tmpdir)
            state = service.create_game(seed=1)

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
                    if '"mission_action"' in text:
                        raise RuntimeError("good mission action should not call provider")
                    return super().chat_completion(profile, messages)

            service = _service(tmpdir, ai_player=AiPlayer(provider=NoMissionPromptProvider()))
            state = service.create_game(seed=4)
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
            state = service.create_game(seed=1)
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

    def test_active_game_state_can_be_restored_from_persisted_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "soloavalon.sqlite3"
            connection = connect_sqlite(database_path)
            try:
                initialize_database(connection)
                service = GameService(connection, ai_player=AiPlayer(provider=_DeterministicProvider()))
                state = service.create_game(seed=1)
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


def _service(tmpdir, ai_player=None):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    if ai_player is None:
        ai_player = AiPlayer(provider=_DeterministicProvider())
    return GameService(connection, ai_player=ai_player)


class _DeterministicProvider:
    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        if '"team"' in text:
            team_size = _team_size(text)
            return json.dumps(
                {
                    "team": _player_ids(text)[:team_size],
                    "private_reason_summary": "测试模型选择合法车队。",
                    "public_message": "我先给一个方便观察票型的车队。",
                },
                ensure_ascii=False,
            )
        if "现在轮到你发言" in text:
            return json.dumps(
                {
                    "public_message": "我先围绕当前车队观察大家的表态，投票分布会很有价值。",
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
        if '"target_player_id"' in text:
            viewer = _viewer_id(text)
            candidates = [player_id for player_id in _player_ids(text) if player_id != viewer]
            return json.dumps(
                {
                    "target_player_id": candidates[0],
                    "private_reason_summary": "测试模型选择合法刺杀目标。",
                    "candidate_ranking": candidates,
                },
                ensure_ascii=False,
            )
        raise RuntimeError("unhandled test prompt")


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
