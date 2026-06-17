import json
import unittest
from dataclasses import fields

from backend.app.ai.context import ContextBuilder
from backend.app.ai.player import AiDecisionError, AiPlayer
from backend.app.game.models import Faction, GameState, MissionAction, MissionConfig, Phase, Player, Role, Vote
from backend.app.game.rules import create_five_player_game, propose_team
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.schemas import parse_json_object
from backend.app.prompting.templates import PromptBuilder


def test_profile() -> LlmProfile:
    kwargs = {
        "id": "profile_1",
        "name": "Test",
        "base_url": "https://api.example.com/v1",
        "api_key": "test-api-key",
        "model": "model",
        "temperature": 0.3,
        "timeout": 5.0,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    if "max_tokens" in {field.name for field in fields(LlmProfile)}:
        kwargs["max_tokens"] = 256
    return LlmProfile(**kwargs)


class PromptingAndProviderTests(unittest.TestCase):
    def test_prompt_builder_uses_stable_prefix_before_dynamic_suffix(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        messages = PromptBuilder().build_messages(context, Phase.SPEECH)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(context.stable_prefix, messages[0]["content"])
        self.assertTrue(any("玩家视角" in message["content"] for message in messages))
        self.assertNotIn(context.dynamic_private_suffix, messages[0]["content"])

    def test_stable_prefix_keeps_only_default_player_and_role_setup(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        self.assertIn("扮演", context.stable_prefix)
        self.assertIn("5 名玩家", context.stable_prefix)
        self.assertIn("梅林", context.stable_prefix)
        self.assertIn("忠臣", context.stable_prefix)
        self.assertIn("刺客", context.stable_prefix)
        self.assertIn("爪牙", context.stable_prefix)
        self.assertNotIn("好人阵营在 3 次任务成功后获胜", context.stable_prefix)
        self.assertNotIn("公开发言要像真人玩家讨论", context.stable_prefix)

    def test_stable_prefix_uses_current_game_role_configuration(self):
        state = GameState(
            players=(
                Player("player_1", 0, "You", True, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "AI 1", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_3", 2, "AI 2", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_4", 3, "AI 3", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_5", 4, "AI 4", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_6", 5, "AI 5", False, Role.MINION, Faction.EVIL),
            ),
            missions=(MissionConfig(round_number=1, team_size=2, fail_cards_required=1),),
        )

        context = ContextBuilder().build(state, "player_2", Phase.SPEECH)

        self.assertIn("6 名玩家", context.stable_prefix)
        self.assertIn("忠臣 3 名", context.stable_prefix)
        self.assertNotIn("5 人局", context.stable_prefix)
        self.assertNotIn("忠臣 2 名", context.stable_prefix)
        self.assertIn("梅林：好人阵营", context.stable_prefix)
        self.assertIn("刺客：恶方阵营", context.stable_prefix)

    def test_prompt_builder_requests_chinese_player_discussion_from_visible_context(self):
        state = create_five_player_game(seed=20)
        events = [
            {"event_type": "speech", "public_payload": {"player_id": "player_1", "message": "我支持这队"}},
            {"event_type": "team_proposed", "public_payload": {"leader": "player_2", "team": ["player_1", "player_2"]}},
        ]
        context = ContextBuilder().build(
            state,
            state.players[1].id,
            Phase.SPEECH,
            public_events=events,
        )

        messages = PromptBuilder().build_messages(context, Phase.SPEECH)
        prompt_text = "\n".join(message["content"] for message in messages)

        self.assertIn("简体中文", prompt_text)
        self.assertIn("正常阿瓦隆玩家", prompt_text)
        self.assertIn("角色信息", prompt_text)
        self.assertIn("对局信息", prompt_text)
        self.assertIn("历史记录", prompt_text)
        self.assertIn("legal_actions", prompt_text)
        self.assertIn("直接输出", prompt_text)

    def test_speech_and_vote_prompts_do_not_include_json_schema(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))

        for phase in (Phase.SPEECH, Phase.VOTING):
            context = ContextBuilder().build(state, state.players[1].id, phase)
            prompt_text = "\n".join(
                message["content"] for message in PromptBuilder().build_messages(context, phase)
            )

            self.assertNotIn("JSON", prompt_text)
            self.assertNotIn("public_message", prompt_text)
            self.assertNotIn("private_reason_summary", prompt_text)
            self.assertNotIn('"vote"', prompt_text)
            self.assertIn("直接输出", prompt_text)

    def test_speech_prompt_discourages_repeated_stock_phrasing(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("不要复述", prompt_text)
        self.assertIn("模板句式", prompt_text)

    def test_prompt_builder_retains_old_events_as_incremental_messages(self):
        state = create_five_player_game(seed=20)
        events = [
            {
                "event_index": 1,
                "event_type": "team_proposed",
                "public_payload": {"leader_player_id": "player_1", "team": ["player_1", "player_2"]},
            },
            {
                "event_index": 2,
                "event_type": "speech",
                "public_payload": {"player_id": "player_2", "message": "我先观察票型"},
            },
        ]
        context = ContextBuilder().build(
            state,
            state.players[1].id,
            Phase.SPEECH,
            public_events=events,
        )

        messages = PromptBuilder().build_messages(context, Phase.SPEECH)
        contents = [message["content"] for message in messages]

        self.assertTrue(any("#1 player_1 提交车队" in content for content in contents))
        self.assertTrue(any("#2 player_2 发言" in content for content in contents))
        self.assertFalse(any("新增公开信息" in content for content in contents))
        self.assertLess(
            next(index for index, content in enumerate(contents) if "#1 player_1 提交车队" in content),
            next(index for index, content in enumerate(contents) if "#2 player_2 发言" in content),
        )
        self.assertTrue(contents[-1].startswith("当前决策"))

    def test_player_facing_prompt_omits_parenthetical_noise(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        events = [
            {
                "event_index": 8,
                "event_type": "team_proposed",
                "public_payload": {"leader_player_id": "player_1", "team": ["player_1", "player_2"]},
            },
        ]
        context = ContextBuilder().build(
            state,
            state.players[1].id,
            Phase.VOTING,
            public_events=events,
        )

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.VOTING)
        )

        self.assertNotIn("新增公开信息", prompt_text)
        self.assertNotIn("（", prompt_text)
        self.assertNotIn("）", prompt_text)
        self.assertNotIn("(You,真人)", prompt_text)
        self.assertNotIn("(AI", prompt_text)

    def test_ai_player_accepts_plain_text_speech_output(self):
        class TextProvider:
            def chat_completion(self, profile, messages):
                return "我觉得这轮先看车队票型，暂时不急着下定论。"

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        speaker = state.players[3]

        result = AiPlayer(provider=TextProvider()).speak(state, speaker.id, test_profile())

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.public_message, "我觉得这轮先看车队票型，暂时不急着下定论。")
        self.assertEqual(result.decision.stance, "uncertain")

    def test_ai_player_accepts_plain_text_vote_output(self):
        class TextProvider:
            def chat_completion(self, profile, messages):
                return "反对，因为这个车队目前缺少足够的公开依据。"

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        voter = state.players[3]

        result = AiPlayer(provider=TextProvider()).vote(state, voter.id, test_profile())

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.vote, Vote.REJECT)
        self.assertIn("缺少足够的公开依据", result.decision.public_reason)

    def test_parse_json_object_accepts_code_fenced_json(self):
        parsed = parse_json_object('```json\n{"vote":"approve"}\n```')

        self.assertEqual(parsed, {"vote": "approve"})

    def test_llm_provider_uses_injectable_transport(self):
        captured = {}

        def transport(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {
                "choices": [
                    {"message": {"content": json.dumps({"vote": "approve"})}}
                ]
            }

        provider = LlmProvider(transport=transport)

        content = provider.chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(content), {"vote": "approve"})
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(captured["payload"]["model"], "model")
        self.assertNotIn("max_tokens", captured["payload"])

    def test_ai_player_raises_when_model_returns_illegal_vote(self):
        class BadProvider:
            def chat_completion(self, profile, messages):
                return json.dumps({"vote": "maybe"})

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        voter = state.players[3]

        with self.assertRaises(AiDecisionError) as captured:
            AiPlayer(provider=BadProvider()).vote(state, voter.id, test_profile())

        self.assertEqual(captured.exception.validation_status, "error")
        self.assertIn('"maybe"', captured.exception.output_raw)
        self.assertGreater(len(captured.exception.prompt_messages), 0)

    def test_ai_player_raises_when_good_player_model_returns_fail(self):
        class BadProvider:
            def chat_completion(self, profile, messages):
                return json.dumps(
                    {
                        "mission_action": "fail",
                        "private_reason_summary": "bad output",
                    }
                )

        state = create_five_player_game(seed=22)
        good_player = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)
        teammate = next(player for player in state.players if player.id != good_player.id)
        state = propose_team(state, state.players[0].id, (good_player.id, teammate.id))

        with self.assertRaises(AiDecisionError) as captured:
            AiPlayer(provider=BadProvider()).mission_action(
                state,
                good_player.id,
                test_profile(),
            )

        self.assertEqual(captured.exception.validation_status, "error")
        self.assertIn('"fail"', captured.exception.output_raw)

    def test_ai_player_raises_when_provider_call_fails(self):
        class FailingProvider:
            def chat_completion(self, profile, messages):
                raise RuntimeError("provider offline")

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        voter = state.players[3]

        with self.assertRaises(AiDecisionError) as captured:
            AiPlayer(provider=FailingProvider()).vote(state, voter.id, test_profile())

        self.assertEqual(captured.exception.validation_status, "error")
        self.assertIsNone(captured.exception.output_raw)
        self.assertIn("provider offline", captured.exception.error_message)
        self.assertTrue(
            any("当前阶段：投票" in message["content"] for message in captured.exception.prompt_messages)
        )
