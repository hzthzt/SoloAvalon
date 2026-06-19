import json
import unittest
from dataclasses import fields

from backend.app.ai.context import ContextBuilder
from backend.app.ai.player import AiDecisionError, AiPlayer
from backend.app.game.models import Faction, GameOption, GameState, MissionAction, MissionConfig, Phase, Player, Role, Vote
from backend.app.game.rules import create_five_player_game, create_game, propose_team
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.config import load_prompt_template_config
from backend.app.prompting.schemas import lady_of_lake_decision_from_output, parse_json_object
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
    def test_default_prompt_config_matches_current_role_setup_and_percival_certainty(self):
        config = load_prompt_template_config()

        self.assertEqual(
            config.recommended_role_setups[7]["evil"],
            ["assassin", "morgana", "oberon"],
        )
        self.assertEqual(
            config.recommended_role_setups[8]["evil"],
            ["assassin", "morgana", "minion"],
        )
        self.assertEqual(
            config.optional_mechanics["lady_of_lake"]["default_enabled_for_player_counts"],
            [8, 9, 10],
        )
        self.assertIn("包含梅林和莫甘娜", config.role_descriptions["percival"])
        self.assertIn("包含梅林和莫甘娜", config.role_gameplay["percival"])
        self.assertIn("包含梅林和莫甘娜", config.extra_information["percival_merlin_candidates"])
        self.assertNotIn("若有莫甘娜", config.role_descriptions["percival"])
        self.assertNotIn("可能包含莫甘娜", config.extra_information["percival_merlin_candidates"])

    def test_default_prompt_config_encourages_real_table_behavior(self):
        config = load_prompt_template_config()
        system_text = "\n".join(config.system_lines)
        propose_text = "\n".join(config.action_prompts["propose_team"]["lines"])
        speak_text = "\n".join(config.action_prompts["speak"]["lines"])
        vote_text = "\n".join(config.action_prompts["vote"]["lines"])

        self.assertIn("积极争取上车", system_text)
        self.assertIn("跳身份", system_text)
        self.assertIn("反跳", system_text)
        self.assertIn("诈身份", system_text)
        self.assertIn("自称身份只能作为待验证信息", system_text)
        self.assertIn("领队默认应考虑自己是否上车", propose_text)
        self.assertIn("可以跳身份", speak_text)
        self.assertIn("想上车", speak_text)
        self.assertIn("上车意愿", vote_text)
        self.assertIn("假跳", config.role_strategy_tips["loyal_servant"])
        self.assertIn("辨别跳身份的真假", config.role_strategy_tips["percival"])
        self.assertIn("跳梅林", config.role_strategy_tips["morgana"])
        self.assertIn("假跳", config.role_strategy_tips["assassin"])

    def test_built_prompt_includes_real_table_behavior_guidance(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[0].id, Phase.TEAM_PROPOSAL)

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.TEAM_PROPOSAL)
        )

        self.assertIn("积极争取上车", prompt_text)
        self.assertIn("跳身份", prompt_text)
        self.assertIn("自称身份只能作为待验证信息", prompt_text)
        self.assertIn("领队默认应考虑自己是否上车", prompt_text)

    def test_player_view_includes_percival_strategy_and_merlin_candidates(self):
        state = GameState(
            players=(
                Player("player_1", 0, "You", True, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "AI 1", False, Role.PERCIVAL, Faction.GOOD),
                Player("player_3", 2, "AI 2", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_4", 3, "AI 3", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_5", 4, "AI 4", False, Role.MORGANA, Faction.EVIL),
            ),
            missions=(MissionConfig(round_number=1, team_size=2, fail_cards_required=1),),
        )

        context = ContextBuilder().build(state, "player_2", Phase.SPEECH)
        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("身份：派西维尔。", prompt_text)
        self.assertIn("身份策略：", prompt_text)
        self.assertIn("梅林候选", prompt_text)
        self.assertIn("包含梅林和莫甘娜", prompt_text)
        self.assertNotIn("若有莫甘娜", prompt_text)
        self.assertNotIn("可能包含莫甘娜", prompt_text)
        self.assertIn("玩家1、玩家5", prompt_text)

    def test_stable_prefix_uses_game_facts_without_prompt_metadata(self):
        state = GameState(
            players=(
                Player("player_1", 0, "You", True, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "AI 1", False, Role.PERCIVAL, Faction.GOOD),
                Player("player_3", 2, "AI 2", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_4", 3, "AI 3", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_5", 4, "AI 4", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_6", 5, "AI 5", False, Role.MORGANA, Faction.EVIL),
                Player("player_7", 6, "AI 6", False, Role.MORDRED, Faction.EVIL),
            ),
            missions=(
                MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
                MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
                MissionConfig(round_number=3, team_size=3, fail_cards_required=1),
                MissionConfig(round_number=4, team_size=4, fail_cards_required=2),
                MissionConfig(round_number=5, team_size=4, fail_cards_required=1),
            ),
            enabled_options=frozenset({GameOption.LADY_OF_LAKE}),
        )

        context = ContextBuilder().build(state, "player_3", Phase.SPEECH)
        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("本局身份：", context.stable_prefix)
        self.assertIn("忠臣 2 名", context.stable_prefix)
        self.assertIn("莫德雷德 1 名", context.stable_prefix)
        self.assertIn("启用扩展机制：", context.stable_prefix)
        self.assertIn("湖中仙女", context.stable_prefix)
        self.assertNotIn("Prompt 模板版本", prompt_text)
        self.assertNotIn("推荐身份组合", prompt_text)
        self.assertNotIn("无推荐身份组合", prompt_text)
        self.assertNotIn("默认不加入推荐身份组合", prompt_text)
        self.assertNotIn("可选扩展身份", prompt_text)
        self.assertNotIn("崔斯坦", context.stable_prefix)
        self.assertNotIn("伊索尔德", context.stable_prefix)

    def test_prompt_builder_uses_stable_prefix_before_dynamic_suffix(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        messages = PromptBuilder().build_messages(context, Phase.SPEECH)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(context.stable_prefix, messages[0]["content"])
        self.assertTrue(any("【你的视角】" in message["content"] for message in messages))
        self.assertNotIn(context.dynamic_private_suffix, messages[0]["content"])

    def test_stable_prefix_keeps_only_default_player_and_role_setup(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        self.assertIn("扮演", context.stable_prefix)
        self.assertIn("【本局配置】", context.stable_prefix)
        self.assertIn("玩家人数：5", context.stable_prefix)
        self.assertIn("阵营人数：好人 3 人，恶方 2 人", context.stable_prefix)
        self.assertIn("第 1 轮：车队 2 人，任务失败需要 1 张失败票", context.stable_prefix)
        self.assertIn("梅林", context.stable_prefix)
        self.assertIn("派西维尔", context.stable_prefix)
        self.assertIn("忠臣", context.stable_prefix)
        self.assertIn("刺客", context.stable_prefix)
        self.assertIn("莫甘娜", context.stable_prefix)
        self.assertNotIn("启用扩展机制", context.stable_prefix)
        self.assertNotIn("SoloAvalon", context.stable_prefix)
        self.assertNotIn("隐藏真相", context.stable_prefix)
        self.assertNotIn("推测未提供", context.stable_prefix)
        self.assertNotIn("好人阵营在 3 次任务成功后获胜", context.stable_prefix)

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

        self.assertIn("玩家人数：6", context.stable_prefix)
        self.assertIn("阵营人数：好人 4 人，恶方 2 人", context.stable_prefix)
        self.assertIn("忠臣 3 名", context.stable_prefix)
        self.assertIn("第 1 轮：车队 2 人，任务失败需要 1 张失败票", context.stable_prefix)
        self.assertNotIn("5 人局", context.stable_prefix)
        self.assertNotIn("忠臣 2 名", context.stable_prefix)
        self.assertIn("梅林 1 名：好人阵营", context.stable_prefix)
        self.assertIn("刺客 1 名：恶方阵营", context.stable_prefix)

    def test_stable_prefix_formats_seven_player_mission_configuration(self):
        state = GameState(
            players=(
                Player("player_1", 0, "You", True, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "AI 1", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_3", 2, "AI 2", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_4", 3, "AI 3", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_5", 4, "AI 4", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_6", 5, "AI 5", False, Role.MINION, Faction.EVIL),
                Player("player_7", 6, "AI 6", False, Role.MINION, Faction.EVIL),
            ),
            missions=(
                MissionConfig(round_number=1, team_size=2, fail_cards_required=1),
                MissionConfig(round_number=2, team_size=3, fail_cards_required=1),
                MissionConfig(round_number=3, team_size=3, fail_cards_required=1),
                MissionConfig(round_number=4, team_size=4, fail_cards_required=2),
                MissionConfig(round_number=5, team_size=4, fail_cards_required=1),
            ),
        )

        context = ContextBuilder().build(state, "player_2", Phase.SPEECH)

        self.assertIn("玩家人数：7", context.stable_prefix)
        self.assertIn("阵营人数：好人 4 人，恶方 3 人", context.stable_prefix)
        self.assertIn("第 4 轮：车队 4 人，任务失败需要 2 张失败票", context.stable_prefix)
        self.assertIn("爪牙 2 名", context.stable_prefix)

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
        self.assertIn("【你的视角】", prompt_text)
        self.assertIn("身份玩法", prompt_text)
        self.assertIn("你的额外信息", prompt_text)
        self.assertIn("【公开记录】", prompt_text)
        self.assertIn("【本次行动】", prompt_text)
        self.assertIn("只返回 JSON", prompt_text)
        self.assertNotIn("当前局面", prompt_text)
        self.assertNotIn("公开时间线", prompt_text)
        self.assertNotIn("当前行动", prompt_text)
        for schema_key in (
            "private_view",
            "public_state",
            "recent_public_events",
            "legal_actions",
        ):
            self.assertNotIn(schema_key, prompt_text)

    def test_prompt_builder_includes_lady_of_lake_action_contract(self):
        base_state = create_game(
            player_count=8,
            seed=8,
            enabled_options={GameOption.LADY_OF_LAKE},
        )
        state = GameState(
            players=base_state.players,
            missions=base_state.missions,
            enabled_options=base_state.enabled_options,
            current_round=3,
            phase=Phase.LADY_OF_LAKE,
            lady_of_lake_holder_player_id="player_8",
            lady_of_lake_previous_holder_ids=("player_8",),
        )

        context = ContextBuilder().build(state, "player_8", Phase.LADY_OF_LAKE)
        messages = PromptBuilder().build_messages(context, Phase.LADY_OF_LAKE)
        prompt_text = "\n".join(message["content"] for message in messages)

        self.assertIn("现在轮到你使用湖中仙女", prompt_text)
        self.assertIn('"target_player_id"', prompt_text)
        self.assertIn("player_1", prompt_text)
        self.assertNotIn("player_8", messages[-1]["content"])

    def test_lady_of_lake_decision_validates_target(self):
        base_state = create_game(
            player_count=8,
            seed=8,
            enabled_options={GameOption.LADY_OF_LAKE},
        )
        state = GameState(
            players=base_state.players,
            missions=base_state.missions,
            enabled_options=base_state.enabled_options,
            current_round=3,
            phase=Phase.LADY_OF_LAKE,
            lady_of_lake_holder_player_id="player_8",
            lady_of_lake_previous_holder_ids=("player_8",),
        )

        decision = lady_of_lake_decision_from_output(
            {
                "target_player_id": "player_1",
                "private_reason_summary": "需要确认 1 号阵营。",
            },
            state,
            "player_8",
        )

        self.assertEqual(decision.target_player_id, "player_1")
        with self.assertRaises(ValueError):
            lady_of_lake_decision_from_output(
                {
                    "target_player_id": "player_8",
                    "private_reason_summary": "非法自查。",
                },
                state,
                "player_8",
            )

    def test_speech_prompt_uses_seat_names_without_internal_player_ids(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        events = [
            {
                "event_index": 1,
                "event_type": "team_proposed",
                "public_payload": {"leader_player_id": "player_1", "team": ["player_1", "player_2"]},
            },
            {
                "event_index": 2,
                "event_type": "speech",
                "public_payload": {"player_id": "player_2", "message": "我支持player_1和player_2。"},
            },
        ]
        context = ContextBuilder().build(
            state,
            state.players[1].id,
            Phase.SPEECH,
            public_events=events,
        )

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("你是 玩家2。", prompt_text)
        self.assertIn("公开玩家：玩家1、玩家2、玩家3、玩家4、玩家5。", prompt_text)
        self.assertIn("#1 第 1 轮，玩家1 提交车队：玩家1、玩家2。", prompt_text)
        self.assertIn("#2 玩家2 发言：我支持玩家1和玩家2。", prompt_text)
        self.assertNotRegex(prompt_text, r"player_\d+")

    def test_speech_and_vote_prompts_use_minimal_json_contracts(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))

        speech_context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)
        speech_prompt = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(speech_context, Phase.SPEECH)
        )
        self.assertIn('{"public_message"', speech_prompt)
        self.assertIn('"private_reason_summary"', speech_prompt)
        self.assertNotIn('"stance"', speech_prompt)

        vote_context = ContextBuilder().build(state, state.players[1].id, Phase.VOTING)
        vote_prompt = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(vote_context, Phase.VOTING)
        )
        self.assertIn('{"vote"', vote_prompt)
        self.assertIn('"private_reason_summary"', vote_prompt)
        self.assertNotIn('"public_reason"', vote_prompt)

    def test_speech_prompt_discourages_repeated_stock_phrasing(self):
        state = create_five_player_game(seed=20)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("不要复述", prompt_text)
        self.assertIn("模板句式", prompt_text)

    def test_prompt_builder_presents_old_events_as_chronological_timeline(self):
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
        timeline = next(content for content in contents if content.startswith("【公开记录】"))

        self.assertIn("#1 第 1 轮，玩家1 提交车队", timeline)
        self.assertIn("#2 玩家2 发言", timeline)
        self.assertFalse(any("新增公开信息" in content for content in contents))
        self.assertLess(
            timeline.index("#1 第 1 轮，玩家1 提交车队"),
            timeline.index("#2 玩家2 发言"),
        )
        self.assertTrue(contents[-1].startswith("【本次行动】"))

    def test_prompt_builder_summarizes_vote_and_quest_results_with_counts(self):
        state = create_five_player_game(seed=20)
        events = [
            {
                "event_index": 1,
                "event_type": "team_proposed",
                "public_payload": {"leader_player_id": "player_1", "team": ["player_1", "player_2"]},
            },
            {"event_index": 2, "event_type": "vote_cast", "public_payload": {"player_id": "player_1", "vote": "approve"}},
            {"event_index": 3, "event_type": "vote_cast", "public_payload": {"player_id": "player_2", "vote": "approve"}},
            {"event_index": 4, "event_type": "vote_cast", "public_payload": {"player_id": "player_3", "vote": "reject"}},
            {"event_index": 5, "event_type": "vote_result", "public_payload": {"approved": True, "failed_team_votes": 0}},
            {"event_index": 6, "event_type": "quest_action_submitted", "public_payload": {"player_id": "player_1"}},
            {"event_index": 7, "event_type": "quest_action_submitted", "public_payload": {"player_id": "player_2"}},
            {
                "event_index": 8,
                "event_type": "quest_result",
                "public_payload": {
                    "quest_results": ["fail"],
                    "success_cards": 1,
                    "fail_cards": 1,
                },
            },
        ]
        context = ContextBuilder().build(
            state,
            state.players[1].id,
            Phase.SPEECH,
            public_events=events,
        )

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("投票结果：车队通过。赞成：玩家1、玩家2；反对：玩家3。", prompt_text)
        self.assertNotIn("player_1 已投票", prompt_text)
        self.assertIn("任务成员 玩家1、玩家2 已提交任务行动。", prompt_text)
        self.assertIn("第 1 轮任务结果：失败。成功票 1，失败票 1。", prompt_text)

    def test_identity_view_uses_role_specific_gameplay_and_extra_information(self):
        state = create_five_player_game(seed=11)
        merlin = next(player for player in state.players if player.role == Role.MERLIN)
        evil_ids = [player.id for player in state.players if player.faction == Faction.EVIL]

        context = ContextBuilder().build(state, merlin.id, Phase.SPEECH)
        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("身份玩法：你知道哪些玩家属于恶方", prompt_text)
        evil_labels = "、".join(f"玩家{int(player_id.split('_')[1])}" for player_id in evil_ids)
        self.assertIn(f"你的额外信息：{evil_labels} 属于恶方。", prompt_text)

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

    def test_ai_player_accepts_json_speech_without_stance(self):
        class JsonProvider:
            def chat_completion(self, profile, messages):
                return json.dumps(
                    {
                        "public_message": "这轮我先看车队结构和后续票型。",
                        "private_reason_summary": "发言阶段不强制给 stance。",
                    },
                    ensure_ascii=False,
                )

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        speaker = state.players[3]

        result = AiPlayer(provider=JsonProvider()).speak(state, speaker.id, test_profile())

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.public_message, "这轮我先看车队结构和后续票型。")
        self.assertEqual(result.decision.stance, "uncertain")

    def test_ai_player_extracts_malformed_speech_json_without_private_summary_leak(self):
        class AlmostJsonProvider:
            def chat_completion(self, profile, messages):
                return (
                    '{"public_message":"第一轮我继续信任player_1和player_2，'
                    '第二轮可以考虑带player_3。",'
                    '"private_reason_summary":"这段不能公开。”}'
                )

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        speaker = state.players[3]

        result = AiPlayer(provider=AlmostJsonProvider()).speak(state, speaker.id, test_profile())

        self.assertEqual(
            result.decision.public_message,
            "第一轮我继续信任玩家1和玩家2，第二轮可以考虑带玩家3。",
        )
        self.assertNotIn("private_reason_summary", result.decision.public_message)
        self.assertNotIn("这段不能公开", result.decision.public_message)

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

    def test_llm_provider_retries_timeout_errors_until_success(self):
        attempts = []

        def transport(url, headers, payload, timeout):
            attempts.append(timeout)
            if len(attempts) < 3:
                raise TimeoutError("The read operation timed out")
            return {
                "choices": [
                    {"message": {"content": json.dumps({"vote": "approve"})}}
                ]
            }

        profile_kwargs = test_profile().__dict__ | {"timeout_retries": 5}
        provider = LlmProvider(transport=transport)

        content = provider.chat_completion(
            LlmProfile(**profile_kwargs),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(content), {"vote": "approve"})
        self.assertEqual(len(attempts), 3)

    def test_llm_provider_stops_after_configured_timeout_retries(self):
        attempts = []

        def transport(url, headers, payload, timeout):
            attempts.append(timeout)
            raise TimeoutError("The read operation timed out")

        profile_kwargs = test_profile().__dict__ | {"timeout_retries": 2}
        provider = LlmProvider(transport=transport)

        with self.assertRaises(TimeoutError):
            provider.chat_completion(
                LlmProfile(**profile_kwargs),
                messages=[{"role": "user", "content": "vote"}],
            )

        self.assertEqual(len(attempts), 3)

    def test_llm_provider_retries_empty_message_content_until_success(self):
        attempts = []

        def transport(url, headers, payload, timeout):
            attempts.append(timeout)
            if len(attempts) < 3:
                return {"choices": [{"message": {"content": ""}}]}
            return {
                "choices": [
                    {"message": {"content": json.dumps({"vote": "approve"})}}
                ]
            }

        profile_kwargs = test_profile().__dict__ | {"timeout_retries": 5}
        provider = LlmProvider(transport=transport)

        content = provider.chat_completion(
            LlmProfile(**profile_kwargs),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(content), {"vote": "approve"})
        self.assertEqual(len(attempts), 3)

    def test_llm_provider_stops_after_configured_empty_content_retries(self):
        attempts = []

        def transport(url, headers, payload, timeout):
            attempts.append(timeout)
            return {"choices": [{"message": {"content": ""}}]}

        profile_kwargs = test_profile().__dict__ | {"timeout_retries": 2}
        provider = LlmProvider(transport=transport)

        with self.assertRaises(ValueError) as captured:
            provider.chat_completion(
                LlmProfile(**profile_kwargs),
                messages=[{"role": "user", "content": "vote"}],
            )

        self.assertIn("non-empty message content", str(captured.exception))
        self.assertEqual(len(attempts), 3)

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
            any("现在轮到你投票" in message["content"] for message in captured.exception.prompt_messages)
        )
