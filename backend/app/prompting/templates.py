from __future__ import annotations

import re
from typing import Any

from backend.app.ai.context import AiContext
from backend.app.game.models import Phase
from backend.app.prompting.config import PromptTemplateConfig, load_prompt_template_config


PROMPT_TEMPLATE_VERSION = load_prompt_template_config().version


class PromptBuilder:
    def __init__(self, prompt_config: PromptTemplateConfig | None = None):
        self._prompt_config = prompt_config or load_prompt_template_config()

    @property
    def version(self) -> str:
        return self._prompt_config.version

    def build_messages(self, context: AiContext, phase: Phase) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": context.stable_prefix,
            },
            {
                "role": "user",
                "content": _player_view_message(context, self._prompt_config),
            },
            *_activity_log_messages(context, self._prompt_config),
            {
                "role": "user",
                "content": _action_message(context, phase, self._prompt_config),
            },
        ]


def _player_view_message(context: AiContext, prompt_config: PromptTemplateConfig) -> str:
    role = context.private_view["viewer_role"]
    faction = context.private_view["viewer_faction"]
    player_labels = _player_label_map(context.public_state["players"])
    return "\n".join(
        [
            prompt_config.section_titles["player_view"],
            prompt_config.labels["player_id"].format(
                player_id=_player_label(context.private_view["viewer_player_id"], player_labels)
            ),
            prompt_config.labels["role"].format(
                role_label=_role_label(role, prompt_config)
            ),
            prompt_config.labels["faction"].format(
                faction_label=_faction_label(faction, prompt_config)
            ),
            prompt_config.labels["role_gameplay"].format(
                gameplay=_role_gameplay_text(role, prompt_config)
            ),
            *_advanced_role_tip_lines(role, prompt_config, context),
            prompt_config.labels["extra_information"].format(
                extra_information=_extra_information_text(context, prompt_config, player_labels)
            ),
            prompt_config.labels["public_players"].format(
                players=_player_ids_text(context.public_state["players"])
            ),
        ]
    )


def _activity_log_messages(
    context: AiContext, prompt_config: PromptTemplateConfig
) -> list[dict[str, str]]:
    player_labels = _player_label_map(context.public_state["players"])
    record_lines = _activity_log_lines(
        _timeline_events(context),
        prompt_config,
        player_labels,
        context,
    )
    return [
        {"role": "user", "content": content}
        for content in ["【活动日志】", *record_lines]
    ]


def _timeline_events(context: AiContext) -> list[dict[str, Any]]:
    records = sorted(context.personal_records, key=lambda record: record["after_event_index"])
    timeline: list[dict[str, Any]] = []
    record_index = 0
    for fallback_index, event in enumerate(context.recent_public_events, start=1):
        event_index = event.get("event_index", fallback_index)
        while (
            record_index < len(records)
            and records[record_index]["after_event_index"] < event_index
        ):
            timeline.append(
                {"event_type": "personal_reason", "public_payload": records[record_index]}
            )
            record_index += 1
        timeline.append(event | {"event_index": event_index})
    timeline.extend(
        {"event_type": "personal_reason", "public_payload": record}
        for record in records[record_index:]
    )
    return _player_visible_events(timeline)


def _action_message(
    context: AiContext,
    phase: Phase,
    prompt_config: PromptTemplateConfig,
) -> str:
    action = str(context.legal_actions.get("action", "none"))
    contract = _temporary_phase_contract(phase, context.legal_actions, prompt_config)
    contract_lines = [contract] if contract else []
    return "\n".join(
        [
            prompt_config.section_titles["action"],
            f"请求你执行 {action}。",
            *_current_state_lines(context, prompt_config),
            *_action_lines(context, prompt_config),
            prompt_config.labels["json_only"],
            *contract_lines,
            f"当前只执行：{action}。",
        ]
    )


def _current_state_lines(context: AiContext, prompt_config: PromptTemplateConfig) -> list[str]:
    state = context.public_state
    player_labels = _player_label_map(state["players"])
    lines = [
        f"当前第 {state['current_round']} 轮，阶段：{state['phase']}。",
        f"队长：{_player_label(state['leader_player_id'], player_labels)}；"
        f"当前车队：{_join_player_labels(state['proposed_team'], player_labels)}。",
        f"连续否决次数：{state['failed_team_votes']}；"
        f"强制组队：{'是' if state['forced_team'] else '否'}。",
    ]
    if state.get("lady_of_lake_holder_player_id"):
        lines.append(
            f"湖中仙女持有者：{_player_label(state['lady_of_lake_holder_player_id'], player_labels)}。"
        )
    lines.extend(_completed_quest_result_reminder_lines(context, prompt_config))
    return lines


def _temporary_phase_contract(
    phase: Phase,
    legal_actions: dict[str, Any],
    prompt_config: PromptTemplateConfig,
) -> str:
    if phase == Phase.ASSASSINATION:
        return prompt_config.action_prompts["assassinate"]["json"]
    if phase == Phase.LADY_OF_LAKE:
        return prompt_config.action_prompts["use_lady_of_lake"]["json"]
    return ""


def _action_lines(
    context: AiContext,
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    legal_actions = context.legal_actions
    action = str(legal_actions.get("action", "none"))
    if action == "propose_team":
        return [
            prompt_config.labels["team_size"].format(
                team_size=_join_or_none(legal_actions.get("team_size"))
            ),
            prompt_config.labels["available_players"].format(
                players=_join_or_none(legal_actions.get("player_ids"))
            ),
        ]
    if action == "speak":
        return []
    if action == "vote":
        return [
            f"当前可选：{_join_or_none(legal_actions.get('votes'))}。",
        ]
    if action == "mission_action":
        return [
            prompt_config.labels["mission_actions"].format(
                actions=_mission_actions_text(
                    legal_actions.get("mission_actions"),
                    prompt_config,
                )
            ),
        ]
    if action == "assassinate":
        return [
            *prompt_config.action_prompts["assassinate"]["lines"],
            prompt_config.labels["assassination_targets"].format(
                targets=_join_or_none(legal_actions.get("target_player_ids"))
            ),
            "本次临时 JSON 格式：",
        ]
    if action == "use_lady_of_lake":
        return [
            *prompt_config.action_prompts["use_lady_of_lake"]["lines"],
            prompt_config.labels["assassination_targets"].format(
                targets=_join_or_none(legal_actions.get("target_player_ids"))
            ),
            "本次临时 JSON 格式：",
        ]
    return list(prompt_config.action_prompts["none"]["lines"])


def _completed_quest_result_reminder_lines(
    context: AiContext,
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    results = _payload_list(context.public_state, "quest_results")
    if not results:
        return []
    result_parts = [
        f"第 {index} 轮{_quest_result_label(result, prompt_config)}"
        for index, result in enumerate(results, start=1)
    ]
    return [
        f"已完成任务结果：{'；'.join(result_parts)}。"
        "投票前请再次重点参考这些任务结果。"
    ]


def _player_visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("event_type") not in {"roles_assigned", "private_view_recorded", "ai_decision"}
    ]


def _activity_log_lines(
    events: list[dict[str, Any]],
    prompt_config: PromptTemplateConfig,
    player_labels: dict[str, str],
    context: AiContext,
) -> list[str]:
    lines: list[str] = []
    pending_votes: list[tuple[str, str]] = []
    completed_quests = 0
    shown_inspections: set[str] = set()

    for fallback_index, event in enumerate(events, start=1):
        event_type = str(event.get("event_type", "unknown"))
        payload = event.get("public_payload")
        public_payload = payload if isinstance(payload, dict) else {}
        prefix = _event_prefix(event, fallback_index)

        if event_type == "vote_cast":
            pending_votes.append(
                (
                    _payload_player_text(public_payload, player_labels, "player_id"),
                    _payload_text(public_payload, "vote"),
                )
            )
            continue

        if event_type == "vote_result":
            settled_votes = public_payload.get("votes")
            votes = (
                [
                    (_player_label(player_id, player_labels), str(vote))
                    for player_id, vote in settled_votes.items()
                ]
                if isinstance(settled_votes, dict)
                else pending_votes
            )
            lines.append(
                f"{prefix} {_vote_result_text(public_payload, votes, completed_quests + 1)}"
            )
            pending_votes = []
            continue

        if event_type == "quest_action_submitted":
            continue

        if event_type == "quest_result":
            completed_quests = _quest_result_round(public_payload, completed_quests)
            lines.append(f"{prefix} {_quest_result_text(public_payload, completed_quests, prompt_config)}")
            continue

        if pending_votes and event_type != "personal_reason":
            pending_votes = []

        if event_type == "team_proposed":
            round_number = completed_quests + 1
            leader = _payload_player_text(public_payload, player_labels, "leader_player_id", "leader")
            team = _join_or_none(_payload_player_list(public_payload, "team", player_labels))
            lines.append(
                f"{prefix} 第 {round_number} 轮，{leader} 提交车队：{team}。"
            )
        elif event_type == "speech":
            player = _payload_player_text(public_payload, player_labels, "player_id")
            message = _replace_player_ids(_payload_text(public_payload, "message"), player_labels)
            lines.append(f"{prefix} {player} 发言：{message}")
        elif event_type == "assassination":
            assassin = _payload_player_text(public_payload, player_labels, "assassin_player_id")
            target = _payload_player_text(public_payload, player_labels, "target_player_id")
            winner = _payload_text(public_payload, "winner")
            suffix = f"胜者：{winner}。" if winner else ""
            lines.append(f"{prefix} {assassin} 刺杀了 {target}。{suffix}".rstrip())
        elif event_type == "lady_of_lake_used":
            viewer = _payload_player_text(public_payload, player_labels, "viewer_player_id")
            target = _payload_player_text(public_payload, player_labels, "target_player_id")
            result = _viewer_lady_of_lake_result(context, public_payload, player_labels, prompt_config)
            lines.append(f"{prefix} {viewer} 使用湖中仙女查看了 {target}。")
            if result:
                lines.append(f"【私有记录】{result}")
                shown_inspections.add(_payload_text(public_payload, "target_player_id"))
        elif event_type == "personal_reason":
            if public_payload.get("player_id") == context.viewer_player_id:
                decision_type = _payload_text(public_payload, "decision_type")
                summary = _replace_player_ids(
                    _payload_text(public_payload, "reason_summary"), player_labels
                )
                lines.append(f"【个人判断记录·{decision_type}】\n{summary}")
        elif event_type == "game_created":
            lines.append(f"{prefix} 对局开始。")
        else:
            lines.append(
                f"{prefix} {_plain_payload_text(public_payload, player_labels)}"
            )

    # Without full public history, keep known inspections without changing the opening view.
    for inspection in context.private_information_events:
        if inspection["target_player_id"] not in shown_inspections:
            target = _player_label(inspection["target_player_id"], player_labels)
            faction = _faction_label(inspection["target_faction"], prompt_config)
            lines.append(
                f"【私有记录】第 {inspection['round_number']} 轮，"
                f"你通过湖中仙女确认：{target} 为{faction}阵营。"
            )

    return lines


def _vote_result_text(
    payload: dict[str, Any],
    votes: list[tuple[str, str]],
    round_number: int,
) -> str:
    approved = "通过" if payload.get("approved") is True else "未通过"
    approvals = [player_id for player_id, vote in votes if vote == "approve"]
    rejections = [player_id for player_id, vote in votes if vote == "reject"]
    if approvals or rejections:
        return (
            f"第 {round_number} 轮投票{approved}。"
            f"赞成：{_join_or_none(approvals)}；反对：{_join_or_none(rejections)}。"
        )
    return f"第 {round_number} 轮投票{approved}。"


def _unsettled_vote_lines(
    votes: list[tuple[str, str]],
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    visible_votes = [(player_id, vote) for player_id, vote in votes if vote]
    if visible_votes:
        return [
            prompt_config.event_templates["unsettled_vote_visible"].format(
                player_id=player_id,
                vote_label=_vote_label(vote, prompt_config),
            )
            for player_id, vote in visible_votes
        ]
    voters = [player_id for player_id, _vote in votes if player_id]
    return [
        prompt_config.event_templates["unsettled_vote_hidden"].format(
            players=_join_or_none(voters)
        )
    ]


def _quest_result_round(payload: dict[str, Any], fallback_completed_quests: int) -> int:
    results = _payload_list(payload, "quest_results")
    if results:
        return len(results)
    return fallback_completed_quests + 1


def _quest_result_text(
    payload: dict[str, Any],
    round_number: int,
    prompt_config: PromptTemplateConfig,
) -> str:
    results = _payload_list(payload, "quest_results")
    result = results[-1] if results else _payload_text(payload, "result")
    result_text = _quest_result_label(result, prompt_config)
    success_cards = payload.get("success_cards")
    fail_cards = payload.get("fail_cards")
    if success_cards is not None and fail_cards is not None:
        return f"第 {round_number} 轮任务{result_text}。成功票 {success_cards}，失败票 {fail_cards}。"
    return f"第 {round_number} 轮任务{result_text}。"


def _event_prefix(event: dict[str, Any], fallback_index: int) -> str:
    event_index = event.get("event_index")
    if isinstance(event_index, int):
        return f"#{event_index:04d}"
    return f"#{fallback_index:04d}"


def _viewer_lady_of_lake_result(
    context: AiContext,
    payload: dict[str, Any],
    player_labels: dict[str, str],
    prompt_config: PromptTemplateConfig,
) -> str:
    if _payload_text(payload, "viewer_player_id") != context.viewer_player_id:
        return ""
    target_id = _payload_text(payload, "target_player_id")
    known_factions = context.private_view.get("lady_of_lake_known_factions", {})
    if not isinstance(known_factions, dict) or target_id not in known_factions:
        return ""
    faction_label = _faction_label(str(known_factions[target_id]), prompt_config)
    return f"湖中仙女查验结果：{_player_label(target_id, player_labels)} 为{faction_label}阵营。"


def _role_gameplay_text(role: object, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.role_gameplay.get(
        str(role),
        prompt_config.role_gameplay.get("default", "按当前身份目标行动。"),
    )


def _role_strategy_text(role: object, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.role_strategy_tips.get(
        str(role),
        prompt_config.role_strategy_tips.get("default", "结合活动日志和身份目标行动。"),
    )


def _advanced_role_tip_lines(
    role: object,
    prompt_config: PromptTemplateConfig,
    context: AiContext,
) -> list[str]:
    if "role_tip_detail" not in context.public_state.get("enabled_options", []):
        return []
    return [
        prompt_config.labels["role_strategy"].format(
            strategy=_role_strategy_text(role, prompt_config)
        )
    ]


def _extra_information_text(
    context: AiContext,
    prompt_config: PromptTemplateConfig,
    player_labels: dict[str, str],
) -> str:
    role = str(context.private_view["viewer_role"])
    known_evil_ids = context.private_view.get("known_evil_player_ids", [])
    merlin_candidate_ids = context.private_view.get("merlin_candidate_player_ids", [])
    known_good_ids = context.private_view.get("known_good_player_ids", [])
    extra_lines: list[str] = []
    if role == "merlin" and known_evil_ids:
        extra_lines.append(
            prompt_config.extra_information["merlin_known_evil"].format(
                players=_join_player_labels(known_evil_ids, player_labels)
            )
        )
    elif role == "percival" and merlin_candidate_ids:
        extra_lines.append(
            prompt_config.extra_information["percival_merlin_candidates"].format(
                players=_join_player_labels(merlin_candidate_ids, player_labels)
            )
        )
    elif known_good_ids:
        extra_lines.append(
            prompt_config.extra_information["known_good"].format(
                players=_join_player_labels(known_good_ids, player_labels)
            )
        )
    elif context.private_view.get("viewer_faction") == "evil" and known_evil_ids:
        extra_lines.append(
            prompt_config.extra_information["evil_teammates"].format(
                players=_join_player_labels(known_evil_ids, player_labels)
            )
        )
    return " ".join(extra_lines) if extra_lines else prompt_config.extra_information["none"]


_PLAYER_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9_])player_(\d+)(?![A-Za-z0-9_])")


def _player_label_map(players: object) -> dict[str, str]:
    if not isinstance(players, list):
        return {}
    labels: dict[str, str] = {}
    for player in players:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("id", ""))
        if not player_id:
            continue
        labels[player_id] = _seat_player_label(player) or _default_player_label(player_id)
    return labels


def _player_label(player_id: object, player_labels: dict[str, str]) -> str:
    text = str(player_id)
    return player_labels.get(text, _default_player_label(text))


def _default_player_label(player_id: str) -> str:
    match = _PLAYER_ID_PATTERN.fullmatch(player_id)
    if match:
        return f"玩家{match.group(1)}"
    return player_id


def _seat_player_label(player: dict[str, Any]) -> str:
    seat_index = player.get("seat_index")
    if isinstance(seat_index, int):
        return f"玩家{seat_index + 1}"
    return ""


def _replace_player_ids(text: str, player_labels: dict[str, str]) -> str:
    def replace_match(match: re.Match[str]) -> str:
        player_id = f"player_{match.group(1)}"
        return player_labels.get(player_id, f"玩家{match.group(1)}")

    return _PLAYER_ID_PATTERN.sub(replace_match, text)


def _player_ids_text(players: object) -> str:
    if not isinstance(players, list):
        return "无"
    parts = []
    for player in players:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("id", ""))
        label = _seat_player_label(player)
        if label:
            parts.append(label)
        elif player_id:
            parts.append(_default_player_label(player_id))
    return _join_or_none(parts)


def _mission_actions_text(actions: object, prompt_config: PromptTemplateConfig) -> str:
    if not isinstance(actions, list):
        return "无"
    return _join_or_none([_quest_result_label(action, prompt_config) for action in actions])


def _role_label(role: object, prompt_config: PromptTemplateConfig) -> str:
    text = str(role)
    return prompt_config.role_labels.get(text, text)


def _faction_label(faction: object, prompt_config: PromptTemplateConfig) -> str:
    text = str(faction)
    return prompt_config.faction_labels.get(text, text)


def _vote_label(vote: str, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.vote_labels.get(vote, vote)


def _quest_result_label(result: object, prompt_config: PromptTemplateConfig) -> str:
    text = str(result)
    return prompt_config.quest_result_labels.get(text, text)


def _payload_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""


def _payload_player_text(
    payload: dict[str, Any],
    player_labels: dict[str, str],
    *keys: str,
) -> str:
    value = _payload_text(payload, *keys)
    return _player_label(value, player_labels) if value else ""


def _payload_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _payload_player_list(
    payload: dict[str, Any],
    key: str,
    player_labels: dict[str, str],
) -> list[str]:
    return [_player_label(item, player_labels) for item in _payload_list(payload, key)]


def _plain_payload_text(payload: dict[str, Any], player_labels: dict[str, str]) -> str:
    if not payload:
        return "无公开详情。"
    return "；".join(
        f"{key}={_replace_player_ids(str(value), player_labels)}"
        for key, value in sorted(payload.items())
    )


def _join_player_labels(values: object, player_labels: dict[str, str]) -> str:
    if isinstance(values, list):
        items = [_player_label(value, player_labels) for value in values if str(value)]
    elif isinstance(values, tuple):
        items = [_player_label(value, player_labels) for value in values if str(value)]
    else:
        items = [_player_label(values, player_labels)] if values else []
    return "、".join(items) if items else "无"


def _join_or_none(values: object) -> str:
    if isinstance(values, list):
        items = [str(value) for value in values if str(value)]
    elif isinstance(values, tuple):
        items = [str(value) for value in values if str(value)]
    else:
        items = [str(values)] if values else []
    return "、".join(items) if items else "无"
