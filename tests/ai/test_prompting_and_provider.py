import json
import urllib.error
import unittest
from dataclasses import fields

from backend.app.ai.context import ContextBuilder
from backend.app.ai.player import AiDecisionError, AiPlayer
from backend.app.game.models import Faction, GameOption, GameState, MissionAction, MissionConfig, Phase, Player, Role, Vote
from backend.app.game.rules import create_five_player_game, create_game, propose_team
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmCompletionResult, LlmProvider
from backend.app.prompting.config import load_prompt_template_config
from backend.app.prompting.schemas import (
    lady_of_lake_decision_from_output,
    parse_json_object,
    vote_decision_from_output,
)
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
        self.assertIn("候选中包含梅林和莫甘娜", config.role_descriptions["percival"])
        self.assertIn("包含梅林和莫甘娜", config.role_descriptions["percival"])
        self.assertIn("包含梅林和莫甘娜", config.extra_information["percival_merlin_candidates"])
        self.assertNotIn("若有莫甘娜", config.role_descriptions["percival"])
        self.assertNotIn("可能包含莫甘娜", config.extra_information["percival_merlin_candidates"])

    def test_default_prompt_discourages_safe_opening_template_speech(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(config.system_lines + config.action_prompts["speak"]["lines"])

        for phrase in ("信息少", "希望任务顺利", "先观察", "积累信息"):
            self.assertIn(phrase, prompt_text)
        self.assertIn("首轮也要点名", prompt_text)
        self.assertIn("车队中的至少一人", prompt_text)
        self.assertIn("改成短句", prompt_text)

    def test_default_prompt_requires_role_specific_table_actions(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("前面已有两人以上支持同一车", speak_prompt)
        self.assertIn("点名追问", speak_prompt)
        self.assertIn("设置条件票", speak_prompt)
        self.assertIn("失败后的归责框架", speak_prompt)

        self.assertIn("已知坏人在车上", config.role_gameplay["merlin"])
        self.assertIn("不能只用“隐藏身份所以支持”", config.role_gameplay["merlin"])
        self.assertIn("软反对", config.role_gameplay["merlin"])
        self.assertIn("条件支持", config.role_gameplay["merlin"])
        self.assertIn("替代车", config.role_gameplay["merlin"])

        self.assertIn("候选关系如何影响本轮态度", config.role_gameplay["percival"])
        self.assertIn("公开至少体现保护、混淆或观察候选反应", config.role_gameplay["percival"])

        for role in ("assassin", "morgana", "minion"):
            self.assertIn("预埋后续叙事钩子", config.role_gameplay[role])
            self.assertIn("失败后优先压谁", config.role_gameplay[role])

    def test_default_prompt_requires_contested_percival_identity_narratives(self):
        config = load_prompt_template_config()

        identity_prompt = "\n".join(
            config.system_lines
            + config.action_prompts["propose_team"]["lines"]
            + config.action_prompts["speak"]["lines"]
            + config.action_prompts["vote"]["lines"]
        )

        for phrase in (
            "身份叙事债",
            "站哪条候选线",
            "拆哪条候选线",
            "反跳谁",
            "逼问谁在保护谁",
            "跳派不是目的",
        ):
            self.assertIn(phrase, identity_prompt)

        self.assertIn("半藏半露地抢候选解释权", config.role_gameplay["percival"])
        self.assertIn("点名一条你要保的线", config.role_gameplay["percival"])
        self.assertIn("点名一条你要拆的线", config.role_gameplay["percival"])
        self.assertIn("假装懂候选关系", config.role_gameplay["morgana"])
        self.assertIn("逼真派西维尔或挡刀忠臣表态", config.role_gameplay["assassin"])
        self.assertIn("反压假派西维尔", config.role_gameplay["loyal_servant"])

    def test_default_prompt_proactively_opens_light_identity_lines(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("无人开身份线时", speak_prompt)
        self.assertIn("不要硬造身份线", speak_prompt)
        self.assertIn("只有你是派西维尔/莫甘娜", speak_prompt)
        self.assertIn("轻开一条身份线", speak_prompt)
        self.assertIn("已有公开车队、票型、任务结果、保人/排人动作可包装", speak_prompt)
        self.assertIn("不要直接说候选身份", speak_prompt)

        self.assertIn("第二轮还没人谈候选", config.role_gameplay["percival"])
        self.assertIn("用公开车队或票型包装", config.role_gameplay["percival"])
        self.assertIn("不说候选身份", config.role_gameplay["percival"])
        self.assertIn("主动开一条假的保护线", config.role_gameplay["morgana"])
        self.assertIn("轻开身份线", config.role_gameplay["loyal_servant"])

    def test_default_prompt_gives_sayable_identity_contest_phrases(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(config.system_lines + config.action_prompts["speak"]["lines"])

        for phrase in (
            "玩家X一直替玩家Y卸压",
            "玩家X急着排玩家Y",
            "我先偏保玩家X这轮上车",
            "这不像普通好人互相评价",
            "必须用自己的话完成一个动作",
            "不要整局复读示例句",
        ):
            self.assertIn(phrase, prompt_text)

        self.assertIn("公开必须落一句桌面短句", config.role_gameplay["percival"])
        self.assertIn("不能只写在私下理由", config.role_gameplay["percival"])

    def test_default_prompt_requires_private_candidate_reason_to_public_action(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("private_reason_summary 提到候选关系", speak_prompt)
        self.assertIn("public_message 必须落一个不泄露的动作", speak_prompt)
        self.assertIn("不能只公开说车队结构", speak_prompt)

        self.assertIn("私下因为候选关系改变态度", config.role_gameplay["percival"])
        self.assertIn("公开必须用不泄露身份的动作表达一部分", config.role_gameplay["percival"])

    def test_default_prompt_rejects_vague_identity_line_actions(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(config.system_lines + config.action_prompts["speak"]["lines"])
        self.assertIn("只说“这条线我先保着/认着/记着”不算完成动作", prompt_text)
        self.assertIn("必须点出至少一名玩家或一个具体行为关系", prompt_text)

        self.assertIn("不能只说“这条线我先保着”", config.role_gameplay["percival"])
        self.assertIn("点出玩家和公开行为", config.role_gameplay["percival"])

    def test_default_prompt_blocks_synonymous_safe_support_phrasing(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(
            config.system_lines
            + config.action_prompts["propose_team"]["lines"]
            + config.action_prompts["speak"]["lines"]
        )

        self.assertNotIn("第一轮先跑一轮看看结果", prompt_text)
        self.assertNotIn("提供视角", prompt_text)
        self.assertNotIn("多一份视角", prompt_text)
        self.assertIn("不要把禁句换成同义安全句", prompt_text)
        self.assertIn("安全套话", prompt_text)
        self.assertIn("支持必须带条件、风险或责任顺序", prompt_text)
        self.assertIn("必须点名当前车成员、被排除者或失败后先问谁", prompt_text)
        self.assertIn("1 到 3 句", prompt_text)

    def test_default_prompt_gives_positive_table_replacements_for_team_phrasing(self):
        config = load_prompt_template_config()

        team_prompt = "\n".join(config.action_prompts["propose_team"]["lines"])
        self.assertIn("别绕成抽象试车", team_prompt)
        self.assertIn("带上玩家X，是因为", team_prompt)
        self.assertIn("没带玩家Y，是要他先解释", team_prompt)
        self.assertIn("这车炸了我先问玩家X", team_prompt)
        self.assertIn("第一轮也按这个格式说", team_prompt)

        system_prompt = "\n".join(config.system_lines)
        self.assertIn("少列禁句，多给替代表达", system_prompt)
        self.assertIn("带谁、暂时不带谁、炸了先问谁、谁这轮别被打死", system_prompt)

    def test_default_prompt_requires_team_template_self_check(self):
        config = load_prompt_template_config()

        team_prompt = "\n".join(config.action_prompts["propose_team"]["lines"])
        self.assertIn("组队套话自检", team_prompt)
        self.assertIn("先跑一轮", team_prompt)
        self.assertIn("看看结果", team_prompt)
        self.assertIn("后续再调整", team_prompt)
        self.assertIn("删掉重写", team_prompt)
        self.assertIn("带上谁、为什么带、暂时不带谁、炸了先问谁", team_prompt)

    def test_default_prompt_reflects_real_percival_table_play_from_research(self):
        config = load_prompt_template_config()

        self.assertIn("不要报拇指", config.role_gameplay["percival"])
        self.assertIn("用逻辑和车位收集信息", config.role_gameplay["percival"])
        self.assertIn("有人抢跳派西维尔", config.role_gameplay["percival"])
        self.assertIn("先判断他是不是候选在挡刀", config.role_gameplay["percival"])
        self.assertIn("不要立刻把候选卖出来", config.role_gameplay["percival"])

    def test_default_prompt_forces_candidate_observation_into_public_pressure(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("观察候选反应不是公开动作", speak_prompt)
        self.assertIn("我关注玩家X怎么接局势", speak_prompt)
        self.assertIn("必须改成可被全桌回应的压力", speak_prompt)
        self.assertIn("让玩家X上车吃压力", speak_prompt)
        self.assertIn("玩家X这轮先别被打死", speak_prompt)
        self.assertIn("玩家Y为什么急着排玩家X", speak_prompt)

        self.assertIn("只说关注某个候选怎么接话不够", config.role_gameplay["percival"])
        self.assertIn("必须把观察改成桌面压力", config.role_gameplay["percival"])

    def test_default_prompt_prioritizes_candidate_relation_pressure(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("优先点候选本人", speak_prompt)
        self.assertIn("谁在推、保、排、跳过候选", speak_prompt)
        self.assertIn("只压无候选关系的玩家不算完成", speak_prompt)
        self.assertIn("玩家X一直把玩家Y往车上推", speak_prompt)
        self.assertIn("玩家X为什么持续拆玩家Y这条成功线", speak_prompt)

        self.assertIn("公开优先追候选本人", config.role_gameplay["percival"])
        self.assertIn("追谁在推、保、拆候选", config.role_gameplay["percival"])

    def test_default_prompt_requires_candidate_callback_self_check(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(config.system_lines + config.action_prompts["speak"]["lines"])
        self.assertIn("候选回扣自检", prompt_text)
        self.assertIn("点到候选本人", prompt_text)
        self.assertIn("推、保、排、跳过候选的人", prompt_text)
        self.assertIn("只压一个无候选关系的车位玩家", prompt_text)
        self.assertIn("重写公开句", prompt_text)

        self.assertIn("每次涉及候选都先做候选回扣自检", config.role_gameplay["percival"])
        self.assertIn("否则先重写 public_message", config.role_gameplay["percival"])

    def test_default_prompt_requires_opening_candidate_callback(self):
        config = load_prompt_template_config()

        speak_prompt = "\n".join(config.action_prompts["speak"]["lines"])
        self.assertIn("首轮候选回扣", speak_prompt)
        self.assertIn("候选领队带你", speak_prompt)
        self.assertIn("排掉另一候选", speak_prompt)
        self.assertIn("公开选择信任某候选", speak_prompt)
        self.assertIn("至少问一句可公开解释的问题", speak_prompt)
        self.assertIn("不能只说信任、配合或认真完成任务", speak_prompt)

        self.assertIn("首轮被候选带上车", config.role_gameplay["percival"])
        self.assertIn("也要用一句可否认问题回扣候选", config.role_gameplay["percival"])

    def test_default_prompt_replaces_vague_perspective_requests(self):
        config = load_prompt_template_config()

        prompt_text = "\n".join(
            config.system_lines
            + config.action_prompts["propose_team"]["lines"]
            + config.action_prompts["speak"]["lines"]
        )

        self.assertIn("不要用“多一个视角”", prompt_text)
        self.assertIn("不要用“观察一轮”", prompt_text)
        self.assertIn("别说“看看反应”", prompt_text)
        self.assertIn("玩家X为什么把玩家Y排在外面", prompt_text)
        self.assertIn("玩家X为什么把玩家Y往车上推", prompt_text)

        self.assertIn("候选领队排掉另一候选", config.role_gameplay["percival"])
        self.assertIn("优先问领队为什么排他", config.role_gameplay["percival"])

    def test_player_view_includes_basic_role_gameplay_and_hides_advanced_tips_by_default(self):
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
        self.assertIn("角色基础玩法：", prompt_text)
        self.assertNotIn("角色进阶玩法：", prompt_text)
        self.assertIn("梅林候选", prompt_text)
        self.assertIn("包含梅林和莫甘娜", prompt_text)
        self.assertNotIn("若有莫甘娜", prompt_text)
        self.assertNotIn("可能包含莫甘娜", prompt_text)
        self.assertIn("玩家1、玩家5", prompt_text)

    def test_player_view_appends_advanced_role_tips_when_advanced_mode_enabled(self):
        state = GameState(
            players=(
                Player("player_1", 0, "You", True, Role.MERLIN, Faction.GOOD),
                Player("player_2", 1, "AI 1", False, Role.PERCIVAL, Faction.GOOD),
                Player("player_3", 2, "AI 2", False, Role.LOYAL_SERVANT, Faction.GOOD),
                Player("player_4", 3, "AI 3", False, Role.ASSASSIN, Faction.EVIL),
                Player("player_5", 4, "AI 4", False, Role.MORGANA, Faction.EVIL),
            ),
            missions=(MissionConfig(round_number=1, team_size=2, fail_cards_required=1),),
            enabled_options=frozenset({GameOption.ROLE_TIP_DETAIL}),
        )

        context = ContextBuilder().build(state, "player_2", Phase.SPEECH)
        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("角色基础玩法：", prompt_text)
        self.assertIn("角色进阶玩法：", prompt_text)
        self.assertLess(
            prompt_text.index("角色基础玩法："),
            prompt_text.index("角色进阶玩法："),
        )
        self.assertIn("为梅林挡刺杀视线", prompt_text)

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
        self.assertIn("阵营人数：好人 3 人，坏人 2 人", context.stable_prefix)
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
        self.assertIn("阵营人数：好人 4 人，坏人 2 人", context.stable_prefix)
        self.assertIn("忠臣 3 名", context.stable_prefix)
        self.assertIn("第 1 轮：车队 2 人，任务失败需要 1 张失败票", context.stable_prefix)
        self.assertNotIn("5 人局", context.stable_prefix)
        self.assertNotIn("忠臣 2 名", context.stable_prefix)
        self.assertIn("梅林 1 名：你是好人", context.stable_prefix)
        self.assertIn("刺客 1 名：你是坏人", context.stable_prefix)

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
        self.assertIn("阵营人数：好人 4 人，坏人 3 人", context.stable_prefix)
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
        self.assertIn("角色基础玩法", prompt_text)
        self.assertIn("你的额外信息", prompt_text)
        self.assertIn("【活动日志】", prompt_text)
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

    def test_prompt_builder_adds_lady_of_lake_action_contract_only_when_requested(self):
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
        stable_text = "\n".join(message["content"] for message in messages[:-1])

        self.assertIn("现在轮到你使用湖中仙女", prompt_text)
        self.assertNotIn("use_lady_of_lake:", stable_text)
        self.assertIn("本次临时 JSON 格式：", messages[-1]["content"])
        self.assertIn('"target_player_id"', messages[-1]["content"])
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
        self.assertIn("#0001 第 1 轮，玩家1 提交车队：玩家1、玩家2。", prompt_text)
        self.assertIn("#0002 玩家2 发言：我支持玩家1和玩家2。", prompt_text)
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
        self.assertNotIn("assassinate:", speech_prompt)
        self.assertNotIn("use_lady_of_lake:", speech_prompt)

        vote_context = ContextBuilder().build(state, state.players[1].id, Phase.VOTING)
        vote_prompt = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(vote_context, Phase.VOTING)
        )
        self.assertIn('{"vote"', vote_prompt)
        self.assertIn('"private_reason_summary"', vote_prompt)
        self.assertNotIn('"public_reason"', vote_prompt)

    def test_vote_prompt_reemphasizes_completed_quest_results_after_first_quest(self):
        base_state = create_five_player_game(seed=20)
        first_vote_context = ContextBuilder().build(base_state, base_state.players[1].id, Phase.VOTING)
        first_vote_action = PromptBuilder().build_messages(first_vote_context, Phase.VOTING)[-1]["content"]

        second_round_state = base_state.__class__(
            players=base_state.players,
            missions=base_state.missions,
            current_round=2,
            phase=Phase.VOTING,
            proposed_team=("player_2", "player_3", "player_4"),
            quest_results=(False,),
        )
        second_vote_context = ContextBuilder().build(
            second_round_state,
            second_round_state.players[1].id,
            Phase.VOTING,
        )
        second_vote_action = PromptBuilder().build_messages(second_vote_context, Phase.VOTING)[-1]["content"]

        self.assertNotIn("已完成任务结果", first_vote_action)
        self.assertIn("已完成任务结果：第 1 轮失败。", second_vote_action)
        self.assertIn("投票前请再次重点参考这些任务结果。", second_vote_action)

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
        timeline = next(content for content in contents if content.startswith("【活动日志】"))

        self.assertIn("#0001 第 1 轮，玩家1 提交车队", timeline)
        self.assertIn("#0002 玩家2 发言", timeline)
        self.assertFalse(any("新增公开信息" in content for content in contents))
        self.assertLess(
            timeline.index("#0001 第 1 轮，玩家1 提交车队"),
            timeline.index("#0002 玩家2 发言"),
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

        self.assertIn("#0005 第 1 轮投票通过。赞成：玩家1、玩家2；反对：玩家3。", prompt_text)
        self.assertNotIn("已完成投票", prompt_text)
        self.assertNotIn("已提交任务行动", prompt_text)
        self.assertIn("#0008 第 1 轮任务失败。成功票 1，失败票 1。", prompt_text)

    def test_activity_log_includes_only_viewer_ai_action_completion(self):
        state = create_five_player_game(seed=20)
        events = [
            {"event_index": 1, "event_type": "game_created", "public_payload": {}},
            {
                "event_index": 2,
                "event_type": "ai_decision",
                "public_payload": {
                    "player_id": "player_2",
                    "decision_type": "speech",
                    "strategy_summary": "不应进入 prompt",
                },
            },
            {
                "event_index": 3,
                "event_type": "ai_decision",
                "public_payload": {
                    "player_id": "player_3",
                    "decision_type": "vote",
                    "strategy_summary": "其他玩家也不应进入 prompt",
                },
            },
        ]
        context = ContextBuilder().build(
            state,
            "player_2",
            Phase.SPEECH,
            public_events=events,
        )

        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("#0001 对局开始。", prompt_text)
        self.assertIn("#0002 speech 已进行处理。", prompt_text)
        self.assertNotIn("#0003 vote 已进行处理。", prompt_text)
        self.assertNotIn("不应进入 prompt", prompt_text)

    def test_identity_view_uses_role_specific_gameplay_and_extra_information(self):
        state = create_five_player_game(seed=11)
        merlin = next(player for player in state.players if player.role == Role.MERLIN)
        evil_ids = [player.id for player in state.players if player.faction == Faction.EVIL]

        context = ContextBuilder().build(state, merlin.id, Phase.SPEECH)
        prompt_text = "\n".join(
            message["content"] for message in PromptBuilder().build_messages(context, Phase.SPEECH)
        )

        self.assertIn("角色基础玩法：你需要秘密帮助好人完成任务", prompt_text)
        self.assertIn("你知道哪些玩家是坏人，但看不到莫德雷德", context.stable_prefix)
        evil_labels = "、".join(f"玩家{int(player_id.split('_')[1])}" for player_id in evil_ids)
        self.assertIn(f"你的额外信息：{evil_labels} 是坏人。", prompt_text)

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

    def test_ai_player_carries_completion_usage(self):
        class UsageProvider:
            def chat_completion(self, profile, messages):
                return LlmCompletionResult(
                    content=json.dumps(
                        {
                            "vote": "approve",
                            "private_reason_summary": "usage test",
                        }
                    ),
                    prompt_tokens=80,
                    completion_tokens=20,
                    total_tokens=100,
                    cached_tokens=40,
                    cache_hit_rate=0.5,
                )

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        result = AiPlayer(provider=UsageProvider()).vote(state, state.players[3].id, test_profile())

        self.assertEqual(result.prompt_tokens, 80)
        self.assertEqual(result.completion_tokens, 20)
        self.assertEqual(result.total_tokens, 100)
        self.assertEqual(result.cached_tokens, 40)
        self.assertEqual(result.cache_hit_rate, 0.5)

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

    def test_vote_json_accepts_missing_private_reason_summary(self):
        decision = vote_decision_from_output({"vote": "approve"})

        self.assertEqual(decision.vote, Vote.APPROVE)
        self.assertEqual(decision.private_reason_summary, "模型未提供私有理由摘要。")

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

        result = provider.chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(result.content), {"vote": "approve"})
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(captured["payload"]["model"], "model")
        self.assertNotIn("max_tokens", captured["payload"])

    def test_llm_provider_returns_openai_usage_when_available(self):
        def transport(url, headers, payload, timeout):
            return {
                "choices": [
                    {"message": {"content": json.dumps({"vote": "approve"})}}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
            }

        result = LlmProvider(transport=transport).chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(result.content), {"vote": "approve"})
        self.assertEqual(result.prompt_tokens, 100)
        self.assertEqual(result.completion_tokens, 25)
        self.assertEqual(result.total_tokens, 125)
        self.assertEqual(result.cached_tokens, 40)
        self.assertEqual(result.cache_hit_rate, 0.4)

    def test_llm_provider_returns_empty_usage_when_provider_omits_usage(self):
        def transport(url, headers, payload, timeout):
            return {"choices": [{"message": {"content": "{}"}}]}

        result = LlmProvider(transport=transport).chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(result.content, "{}")
        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)
        self.assertIsNone(result.total_tokens)
        self.assertIsNone(result.cached_tokens)
        self.assertIsNone(result.cache_hit_rate)

    def test_llm_provider_rejects_invalid_base_url_before_transport(self):
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append(url)
            return {"choices": [{"message": {"content": "{}"}}]}

        profile = object.__new__(LlmProfile)
        object.__setattr__(profile, "id", "bad_profile")
        object.__setattr__(profile, "name", "Bad")
        object.__setattr__(profile, "base_url", "file:///v1")
        object.__setattr__(profile, "api_key", "test-api-key")
        object.__setattr__(profile, "model", "model")
        object.__setattr__(profile, "temperature", 0.3)
        object.__setattr__(profile, "timeout", 5.0)
        object.__setattr__(profile, "timeout_retries", 0)
        object.__setattr__(profile, "created_at", "2026-06-15T00:00:00Z")
        object.__setattr__(profile, "updated_at", "2026-06-15T00:00:00Z")

        with self.assertRaisesRegex(ValueError, "base_url must start with http:// or https://"):
            LlmProvider(transport=transport).chat_completion(profile, messages=[])

        self.assertEqual(calls, [])

    def test_llm_provider_describes_urlopen_file_errors_as_local_ssl_or_proxy_path_issues(self):
        def transport(url, headers, payload, timeout):
            raise urllib.error.URLError(FileNotFoundError(2, "No such file or directory"))

        with self.assertRaisesRegex(ConnectionError, "local SSL certificate or proxy path"):
            LlmProvider(transport=transport).chat_completion(
                test_profile(),
                messages=[{"role": "user", "content": "vote"}],
            )

    def test_llm_provider_includes_runtime_diagnostics_for_connection_errors(self):
        def transport(url, headers, payload, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(10061, "connection refused"))

        with self.assertRaises(ConnectionError) as context:
            LlmProvider(transport=transport).chat_completion(
                test_profile(),
                messages=[{"role": "user", "content": "vote"}],
            )

        message = str(context.exception)
        self.assertIn("error_type=ConnectionRefusedError", message)
        self.assertIn("python_executable=", message)
        self.assertIn("proxies=", message)
        self.assertNotIn("test-api-key", message)

    def test_llm_provider_describes_http_errors_with_status_code(self):
        def transport(url, headers, payload, timeout):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)

        with self.assertRaisesRegex(ConnectionError, "llm endpoint returned HTTP 401"):
            LlmProvider(transport=transport).chat_completion(
                test_profile(),
                messages=[{"role": "user", "content": "vote"}],
            )

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

        result = provider.chat_completion(
            LlmProfile(**profile_kwargs),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(result.content), {"vote": "approve"})
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

        result = provider.chat_completion(
            LlmProfile(**profile_kwargs),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(result.content), {"vote": "approve"})
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

    def test_ai_player_retries_invalid_json_until_valid_decision(self):
        attempts = []

        class RetryProvider:
            def chat_completion(self, profile, messages):
                attempts.append(messages)
                if len(attempts) == 1:
                    return "{not json"
                return json.dumps(
                    {
                        "team": ["player_1", "player_2"],
                        "public_message": "先开一个常规两人车。",
                    },
                    ensure_ascii=False,
                )

        state = create_five_player_game(seed=21)
        profile = LlmProfile(**(test_profile().__dict__ | {"timeout_retries": 1}))

        result = AiPlayer(provider=RetryProvider()).propose_team(
            state,
            state.players[0].id,
            profile,
        )

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.team, ("player_1", "player_2"))
        self.assertEqual(len(attempts), 2)

    def test_ai_player_retries_missing_business_field_until_valid_decision(self):
        attempts = []

        class RetryProvider:
            def chat_completion(self, profile, messages):
                attempts.append(messages)
                if len(attempts) == 1:
                    return json.dumps({})
                return json.dumps({"vote": "approve"})

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        profile = LlmProfile(**(test_profile().__dict__ | {"timeout_retries": 1}))

        result = AiPlayer(provider=RetryProvider()).vote(state, state.players[3].id, profile)

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.vote, Vote.APPROVE)
        self.assertEqual(len(attempts), 2)

    def test_ai_player_retries_invalid_enum_until_valid_decision(self):
        attempts = []

        class RetryProvider:
            def chat_completion(self, profile, messages):
                attempts.append(messages)
                if len(attempts) == 1:
                    return json.dumps({"vote": "maybe"})
                return json.dumps({"vote": "reject"})

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        profile = LlmProfile(**(test_profile().__dict__ | {"timeout_retries": 1}))

        result = AiPlayer(provider=RetryProvider()).vote(state, state.players[3].id, profile)

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.vote, Vote.REJECT)
        self.assertEqual(len(attempts), 2)

    def test_ai_player_retries_rule_illegal_output_until_valid_decision(self):
        attempts = []

        class RetryProvider:
            def chat_completion(self, profile, messages):
                attempts.append(messages)
                if len(attempts) == 1:
                    return json.dumps({"mission_action": "fail"})
                return json.dumps({"mission_action": "success"})

        state = create_five_player_game(seed=22)
        good_player = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)
        teammate = next(player for player in state.players if player.id != good_player.id)
        state = propose_team(state, state.players[0].id, (good_player.id, teammate.id))
        profile = LlmProfile(**(test_profile().__dict__ | {"timeout_retries": 1}))

        result = AiPlayer(provider=RetryProvider()).mission_action(state, good_player.id, profile)

        self.assertEqual(result.validation_status, "valid")
        self.assertEqual(result.decision.mission_action, MissionAction.SUCCESS)
        self.assertEqual(len(attempts), 2)

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
