from __future__ import annotations

from typing import Any

from backend.app.ai.context import AiContext
from backend.app.game.models import Phase


PROMPT_TEMPLATE_VERSION = "prompt.v4"


class PromptBuilder:
    def build_messages(self, context: AiContext, phase: Phase) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": (
                    f"{context.stable_prefix}\n\n"
                    f"Prompt 模板版本：{PROMPT_TEMPLATE_VERSION}\n"
                    f"{_phase_instruction(phase)}"
                ),
            },
            {
                "role": "user",
                "content": _initial_context_message(context),
            },
        ]
        for default_index, event in enumerate(_player_visible_events(context.recent_public_events), start=1):
            messages.append(
                {
                    "role": "user",
                    "content": _public_event_message(event, default_index),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": _current_decision_message(context, phase),
            }
        )
        return messages


def _phase_instruction(phase: Phase) -> str:
    if phase == Phase.SPEECH:
        return (
            "当前阶段：发言。\n"
            "请直接输出你要公开说出的一段中文发言。"
            "发言应像正常阿瓦隆玩家讨论当前局势，结合你能看到的角色信息、对局信息和历史记录。"
            "不要复述前面玩家的模板句式，要给出自己的判断、疑点或推进理由。"
        )
    if phase == Phase.VOTING:
        return (
            "当前阶段：投票。\n"
            "请直接输出“赞成”或“反对”，可以在后面附上一句中文公开理由。"
            "理由只能引用正常玩家可见的信息。"
        )
    return f"当前阶段 JSON 输出格式：\n{_phase_contract(phase)}"


def _phase_contract(phase: Phase) -> str:
    if phase == Phase.TEAM_PROPOSAL:
        return (
            '{"team":["player_id"],'
            '"private_reason_summary":"1 到 2 句中文，说明你基于哪些可见信息组队",'
            '"public_message":"2 到 4 句中文公开发言，解释车队思路并邀请其他玩家表态"}'
        )
    if phase == Phase.SPEECH:
        return (
            '{"public_message":"2 到 4 句中文公开发言，结合当前车队、发言历史、投票或任务结果表达判断",'
            '"stance":"support_team|oppose_team|uncertain",'
            '"private_reason_summary":"1 到 2 句中文，概括本次发言策略和依据"}'
        )
    if phase == Phase.VOTING:
        return (
            '{"vote":"approve|reject",'
            '"private_reason_summary":"1 到 2 句中文，说明投票策略和依据",'
            '"public_reason":"1 到 3 句中文公开理由，必须只引用正常玩家可见信息"}'
        )
    if phase == Phase.QUEST:
        return (
            '{"mission_action":"success|fail",'
            '"private_reason_summary":"1 到 2 句中文，说明任务行动策略。好人只能选择 success"}'
        )
    if phase == Phase.ASSASSINATION:
        return (
            '{"target_player_id":"player_id",'
            '"private_reason_summary":"2 到 4 句中文，结合全局公开历史和可见信息判断谁最像梅林",'
            '"candidate_ranking":["player_id"]}'
        )
    return "{}"


def _initial_context_message(context: AiContext) -> str:
    return "\n".join(
        [
            "玩家视角：",
            f"- 角色信息 private_view：你是 {context.private_view['viewer_player_id']}，角色 {_role_label(context.private_view['viewer_role'])}，阵营 {_faction_label(context.private_view['viewer_faction'])}。",
            f"- 可见身份：{_visible_roles_text(context.private_view['visible_roles'])}。",
            f"- 已知恶方玩家：{_join_or_none(context.private_view['known_evil_player_ids'])}。",
            f"- 对局信息 public_state：公开玩家为 {_player_ids_text(context.public_state['players'])}。",
            "- 历史记录 recent_public_events：已公开事件按时间顺序保留。",
        ]
    )


def _current_decision_message(context: AiContext, phase: Phase) -> str:
    public_state = context.public_state
    return "\n".join(
        [
            "当前决策：",
            f"- 对局信息：第 {public_state['current_round']} 轮，当前阶段 {_phase_label(public_state['phase'])}，队长 {public_state['leader_player_id']}。",
            f"- 当前车队：{_join_or_none(public_state['proposed_team'])}。",
            f"- 当前发言：{_speeches_text(public_state['speeches'])}。",
            f"- 当前投票进度：{public_state['votes_cast_count']} 票已提交；累计拒绝组队 {public_state['failed_team_votes']} 次。",
            f"- 任务结果：{_join_or_none(public_state['quest_results'])}。",
            *_legal_action_lines(context.legal_actions),
            f"- 本次任务：{_decision_action_label(phase)}。请只基于以上信息作答。",
        ]
    )


def _player_visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("event_type") not in {"private_view_recorded", "ai_decision"}
    ]


def _public_event_message(event: dict[str, Any], default_index: int) -> str:
    event_index = event.get("event_index", default_index)
    return f"#{event_index} {_event_text(event)}"


def _event_text(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type", "unknown"))
    payload = event.get("public_payload")
    public_payload = payload if isinstance(payload, dict) else {}
    if event_type == "game_created":
        return "对局创建。"
    if event_type == "roles_assigned":
        return "身份已经分配。"
    if event_type == "team_proposed":
        leader = _payload_text(public_payload, "leader_player_id", "leader")
        team = _join_or_none(_payload_list(public_payload, "team"))
        return f"{leader} 提交车队：{team}。"
    if event_type == "speech":
        player = _payload_text(public_payload, "player_id")
        message = _payload_text(public_payload, "message")
        return f"{player} 发言：{message}"
    if event_type == "vote_cast":
        player = _payload_text(public_payload, "player_id")
        vote = _payload_text(public_payload, "vote")
        return f"{player} 已投票" + (f"：{_vote_label(vote)}。" if vote else "，票型尚未公开。")
    if event_type == "vote_result":
        approved = "通过" if public_payload.get("approved") is True else "未通过"
        failed_votes = _payload_text(public_payload, "failed_team_votes")
        return f"投票结果：车队{approved}，累计拒绝组队 {failed_votes} 次。"
    if event_type == "quest_action_submitted":
        return f"{_payload_text(public_payload, 'player_id')} 已提交任务行动，具体行动未公开。"
    if event_type == "quest_result":
        results = [_quest_result_label(item) for item in _payload_list(public_payload, "quest_results")]
        winner = _payload_text(public_payload, "winner")
        return f"任务结果更新：{_join_or_none(results)}" + (f"，胜者 {winner}。" if winner else "。")
    if event_type == "assassination":
        assassin = _payload_text(public_payload, "assassin_player_id")
        target = _payload_text(public_payload, "target_player_id")
        winner = _payload_text(public_payload, "winner")
        return f"{assassin} 刺杀 {target}" + (f"，胜者 {winner}。" if winner else "。")
    return f"{event_type}：{_plain_payload_text(public_payload)}"


def _visible_roles_text(visible_roles: object) -> str:
    if not isinstance(visible_roles, dict):
        return "无"
    parts = [
        f"{player_id}={_role_label(role)}"
        for player_id, role in sorted(visible_roles.items())
        if role
    ]
    return _join_or_none(parts)


def _player_ids_text(players: object) -> str:
    if not isinstance(players, list):
        return "无"
    parts = [str(player.get("id", "")) for player in players if isinstance(player, dict)]
    return _join_or_none(parts)


def _speeches_text(speeches: object) -> str:
    if not isinstance(speeches, dict) or not speeches:
        return "无"
    return "；".join(f"{player_id}: {message}" for player_id, message in speeches.items())


def _legal_action_lines(legal_actions: dict[str, Any]) -> list[str]:
    action = str(legal_actions.get("action", "none"))
    lines = [f"- legal_actions：{action}"]
    if action == "propose_team":
        lines.append(f"- 车队人数：{_join_or_none(legal_actions.get('team_size'))}")
        lines.append(f"- 可选玩家：{_join_or_none(legal_actions.get('player_ids'))}")
    elif action == "mission_action":
        lines.append(f"- 可选任务行动：{_join_or_none(legal_actions.get('mission_actions'))}")
    elif action == "assassinate":
        lines.append(f"- 可选目标：{_join_or_none(legal_actions.get('target_player_ids'))}")
    return lines


def _decision_action_label(phase: Phase) -> str:
    labels = {
        Phase.TEAM_PROPOSAL: "组队",
        Phase.SPEECH: "发言",
        Phase.VOTING: "投票",
        Phase.QUEST: "任务行动",
        Phase.ASSASSINATION: "刺杀",
    }
    return labels.get(phase, phase.value)


def _phase_label(phase: object) -> str:
    labels = {
        "team_proposal": "组队",
        "speech": "发言",
        "voting": "投票",
        "quest": "任务",
        "assassination": "刺杀",
        "complete": "结束",
    }
    text = str(phase)
    return labels.get(text, text)


def _role_label(role: object) -> str:
    labels = {
        "merlin": "梅林",
        "loyal_servant": "忠臣",
        "assassin": "刺客",
        "minion": "爪牙",
        "unknown_evil": "未知恶方",
    }
    text = str(role)
    return labels.get(text, text)


def _faction_label(faction: object) -> str:
    labels = {"good": "好人", "evil": "恶方"}
    text = str(faction)
    return labels.get(text, text)


def _vote_label(vote: str) -> str:
    labels = {"approve": "赞成", "reject": "反对"}
    return labels.get(vote, vote)


def _quest_result_label(result: object) -> str:
    labels = {"success": "成功", "fail": "失败"}
    text = str(result)
    return labels.get(text, text)


def _payload_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _plain_payload_text(payload: dict[str, Any]) -> str:
    if not payload:
        return "无公开详情。"
    return "；".join(f"{key}={value}" for key, value in sorted(payload.items()))


def _join_or_none(values: object) -> str:
    if isinstance(values, list):
        items = [str(value) for value in values if str(value)]
    elif isinstance(values, tuple):
        items = [str(value) for value in values if str(value)]
    else:
        items = [str(values)] if values else []
    return "、".join(items) if items else "无"
