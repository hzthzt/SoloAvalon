from dataclasses import replace
import hashlib
import unittest

from backend.app.ai.context import ContextBuilder
from backend.app.game.models import GameOption, LadyOfLakeInspection, Phase
from backend.app.game.rules import create_five_player_game, create_game
from backend.app.prompting.config import load_prompt_template_config
from backend.app.prompting.templates import PromptBuilder


class PromptHistoryTests(unittest.TestCase):
    def messages(self, state, viewer="player_2", events=None, records=None, phase=Phase.SPEECH):
        context = ContextBuilder().build(
            state, viewer, phase, public_events=events, personal_records=records,
        )
        return PromptBuilder().build_messages(context, phase)

    def test_public_prefix_is_identical_across_players_and_common_actions(self):
        state = create_five_player_game(seed=20)
        prefix = self.messages(state)[0]
        for phase in (Phase.TEAM_PROPOSAL, Phase.SPEECH, Phase.VOTING, Phase.QUEST):
            for player in state.players:
                context = ContextBuilder().build(state, player.id, phase)
                messages = PromptBuilder().build_messages(context, phase)
                self.assertEqual(messages[0], prefix)
                self.assertEqual(
                    context.stable_prefix_hash,
                    hashlib.sha256(prefix["content"].encode("utf-8")).hexdigest(),
                )
                self.assertNotIn("【动作规则】", messages[-1]["content"])
        self.assertLess(prefix["content"].index("【动作规则】"), prefix["content"].index("【本局配置】"))
        self.assertIn("【动作 JSON 格式声明】", prefix["content"])

    def test_fixed_rules_and_contract_are_not_repeated_in_action_tail(self):
        config = load_prompt_template_config()
        state = create_five_player_game(seed=20)
        for phase in (Phase.TEAM_PROPOSAL, Phase.SPEECH, Phase.VOTING, Phase.QUEST):
            messages = self.messages(state, phase=phase)
            for action in ("propose_team", "speak", "vote", "mission_action"):
                for rule in config.action_prompts[action]["lines"]:
                    self.assertEqual(messages[0]["content"].count(rule), 1)
                    self.assertNotIn(rule, messages[-1]["content"])
            self.assertNotIn('"private_reason_summary"', messages[-1]["content"])

    def test_append_keeps_previous_messages_and_personal_records_in_order(self):
        state = create_five_player_game(seed=20)
        events = [
            {"event_index": 1, "event_type": "game_created", "public_payload": {}},
            {"event_index": 2, "event_type": "speech",
             "public_payload": {"player_id": "player_1", "message": "earlier speech"}},
        ]
        records = [
            {"player_id": "player_2", "decision_type": "speech",
             "reason_summary": "earlier private judgment", "after_event_index": 1},
        ]
        before = self.messages(state, events=events, records=records)
        records.append({
            "player_id": "player_2", "decision_type": "vote",
            "reason_summary": "new private judgment", "after_event_index": 2,
        })
        events.append({
            "event_index": 3, "event_type": "vote_cast",
            "public_payload": {"player_id": "player_2"},
        })
        after = self.messages(
            replace(state, current_round=2), events=events, records=records, phase=Phase.VOTING,
        )
        self.assertEqual(before[:-1], after[:len(before) - 1])
        text = "\n".join(message["content"] for message in after[2:-1])
        self.assertLess(text.index("earlier private judgment"), text.index("earlier speech"))
        self.assertLess(text.index("earlier speech"), text.index("new private judgment"))
        self.assertNotIn("暂无活动", "\n".join(m["content"] for m in before))

    def test_new_lake_information_is_appended_without_rewriting_opening_view(self):
        state = create_game(player_count=8, seed=8, enabled_options={GameOption.LADY_OF_LAKE})
        events = [{"event_index": 1, "event_type": "game_created", "public_payload": {}}]
        before = self.messages(state, viewer="player_8", events=events)
        inspection = LadyOfLakeInspection("player_8", "player_1", state.players[0].faction, 2)
        later = replace(state, current_round=3, lady_of_lake_inspections=(inspection,))
        events.append({
            "event_index": 2, "event_type": "lady_of_lake_used",
            "public_payload": {"viewer_player_id": "player_8", "target_player_id": "player_1",
                               "round_number": 2},
        })
        after = self.messages(later, viewer="player_8", events=events)
        self.assertEqual(before[:-1], after[:len(before) - 1])
        self.assertEqual(before[1], after[1])
        self.assertNotIn("湖中仙女", after[1]["content"])
        self.assertEqual(sum(m["content"].startswith("【私有记录】") for m in after), 1)
        self.assertIn("湖中仙女查验结果", after[-2]["content"])
        other = self.messages(later, viewer="player_1", events=events)
        self.assertFalse(any(m["content"].startswith("【私有记录】") for m in other))

    def test_personal_records_do_not_consume_fallback_public_event_numbers(self):
        state = create_five_player_game(seed=20)
        events = [
            {"event_type": "game_created", "public_payload": {}},
            {"event_type": "speech", "public_payload": {
                "player_id": "player_1", "message": "public speech",
            }},
        ]
        records = [{
            "player_id": "player_2", "decision_type": "speech",
            "reason_summary": "my earlier judgment", "after_event_index": 1,
        }]
        messages = self.messages(state, events=events, records=records)
        self.assertTrue(messages[3]["content"].startswith("#0001"))
        self.assertTrue(messages[4]["content"].startswith("【个人判断记录"))
        self.assertTrue(messages[5]["content"].startswith("#0002"))

    def test_lake_information_survives_missing_or_truncated_public_history(self):
        state = create_game(player_count=8, seed=8, enabled_options={GameOption.LADY_OF_LAKE})
        state = replace(state, lady_of_lake_inspections=(
            LadyOfLakeInspection("player_8", "player_1", state.players[0].faction, 2),
        ))
        for events in ([], [
            {"event_index": 50, "event_type": "speech",
             "public_payload": {"player_id": "player_2", "message": "later speech"}},
        ]):
            with self.subTest(events=events):
                messages = self.messages(state, viewer="player_8", events=events)
                self.assertNotIn("湖中仙女", messages[1]["content"])
                self.assertTrue(any("第 2 轮，你通过湖中仙女确认" in m["content"] for m in messages[2:-1]))

    def test_current_state_stays_at_tail_without_unsettled_votes_or_cards(self):
        state = replace(
            create_five_player_game(seed=20), current_round=2,
            proposed_team=("player_1", "player_2", "player_3"),
            quest_results=(False,), failed_team_votes=2,
        )
        events = [
            {"event_index": 1, "event_type": "vote_cast",
             "public_payload": {"player_id": "player_1", "vote": "reject"}},
            {"event_index": 2, "event_type": "quest_action_submitted",
             "public_payload": {"player_id": "player_2", "mission_action": "fail"}},
        ]
        messages = self.messages(state, events=events, phase=Phase.VOTING)
        tail = messages[-1]["content"]
        self.assertIn("当前第 2 轮", tail)
        self.assertIn("当前车队：玩家1、玩家2、玩家3", tail)
        self.assertIn("连续否决次数：2", tail)
        self.assertIn("第 1 轮失败", tail)
        self.assertEqual(messages[2:-1], [{"role": "user", "content": "【活动日志】"}])

    def test_settled_vote_mapping_renders_without_unsettled_vote_values(self):
        state = create_five_player_game(seed=20)
        events = [
            {"event_index": 1, "event_type": "vote_cast", "public_payload": {"player_id": "player_1"}},
            {"event_index": 2, "event_type": "vote_result", "public_payload": {
                "approved": False, "votes": {"player_1": "reject", "player_2": "approve"},
            }},
        ]
        messages = self.messages(state, events=events)
        self.assertIn("赞成：玩家2；反对：玩家1", messages[-2]["content"])


if __name__ == "__main__":
    unittest.main()
