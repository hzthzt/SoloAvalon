from __future__ import annotations

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
            {
                "role": "user",
                "content": _public_record_message(context, self._prompt_config),
            },
            {
                "role": "user",
                "content": _action_message(context, phase, self._prompt_config),
            },
        ]


def _player_view_message(context: AiContext, prompt_config: PromptTemplateConfig) -> str:
    role = context.private_view["viewer_role"]
    faction = context.private_view["viewer_faction"]
    return "\n".join(
        [
            prompt_config.section_titles["player_view"],
            prompt_config.labels["player_id"].format(
                player_id=context.private_view["viewer_player_id"]
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
            prompt_config.labels["role_strategy"].format(
                strategy=_role_strategy_text(role, prompt_config)
            ),
            *_role_detail_tip_lines(role, prompt_config),
            prompt_config.labels["extra_information"].format(
                extra_information=_extra_information_text(context, prompt_config)
            ),
            prompt_config.labels["public_players"].format(
                players=_player_ids_text(context.public_state["players"])
            ),
        ]
    )


def _public_record_message(context: AiContext, prompt_config: PromptTemplateConfig) -> str:
    lines = [prompt_config.section_titles["public_record"]]
    record_lines = _public_record_lines(
        _player_visible_events(context.recent_public_events),
        prompt_config,
    )
    if not record_lines:
        lines.append(prompt_config.labels["empty_public_record"])
    else:
        lines.extend(f"#{index} {line}" for index, line in enumerate(record_lines, start=1))
    return "\n".join(lines)


def _action_message(
    context: AiContext,
    phase: Phase,
    prompt_config: PromptTemplateConfig,
) -> str:
    return "\n".join(
        [
            prompt_config.section_titles["action"],
            *_action_lines(context.legal_actions, prompt_config),
            prompt_config.labels["json_only"],
            _phase_contract(phase, context.legal_actions, prompt_config),
        ]
    )


def _phase_contract(
    phase: Phase,
    legal_actions: dict[str, Any],
    prompt_config: PromptTemplateConfig,
) -> str:
    if phase == Phase.TEAM_PROPOSAL:
        return prompt_config.action_prompts["propose_team"]["json"]
    if phase == Phase.SPEECH:
        return prompt_config.action_prompts["speak"]["json"]
    if phase == Phase.VOTING:
        return prompt_config.action_prompts["vote"]["json"]
    if phase == Phase.QUEST:
        actions = _join_or_none(legal_actions.get("mission_actions"))
        return prompt_config.action_prompts["mission_action"]["json"].replace(
            "{mission_actions}",
            actions.replace("、", "|"),
        )
    if phase == Phase.ASSASSINATION:
        return prompt_config.action_prompts["assassinate"]["json"]
    return prompt_config.action_prompts["none"]["json"]


def _action_lines(
    legal_actions: dict[str, Any],
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    action = str(legal_actions.get("action", "none"))
    if action == "propose_team":
        return [
            *prompt_config.action_prompts["propose_team"]["lines"],
            prompt_config.labels["team_size"].format(
                team_size=_join_or_none(legal_actions.get("team_size"))
            ),
            prompt_config.labels["available_players"].format(
                players=_join_or_none(legal_actions.get("player_ids"))
            ),
        ]
    if action == "speak":
        return list(prompt_config.action_prompts["speak"]["lines"])
    if action == "vote":
        return list(prompt_config.action_prompts["vote"]["lines"])
    if action == "mission_action":
        return [
            *prompt_config.action_prompts["mission_action"]["lines"],
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
        ]
    return list(prompt_config.action_prompts["none"]["lines"])


def _player_visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event.get("event_type")
        not in {"game_created", "roles_assigned", "private_view_recorded", "ai_decision"}
    ]


def _public_record_lines(
    events: list[dict[str, Any]],
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    lines: list[str] = []
    pending_votes: list[tuple[str, str]] = []
    pending_quest_submitters: list[str] = []
    completed_quests = 0

    for event in events:
        event_type = str(event.get("event_type", "unknown"))
        payload = event.get("public_payload")
        public_payload = payload if isinstance(payload, dict) else {}

        if event_type == "vote_cast":
            pending_votes.append(
                (
                    _payload_text(public_payload, "player_id"),
                    _payload_text(public_payload, "vote"),
                )
            )
            continue

        if event_type == "vote_result":
            lines.append(_vote_result_text(public_payload, pending_votes, prompt_config))
            pending_votes = []
            continue

        if event_type == "quest_action_submitted":
            submitter = _payload_text(public_payload, "player_id")
            if submitter:
                pending_quest_submitters.append(submitter)
            continue

        if event_type == "quest_result":
            if pending_quest_submitters:
                lines.append(
                    prompt_config.event_templates["quest_submitted"].format(
                        players=_join_or_none(pending_quest_submitters)
                    )
                )
                pending_quest_submitters = []
            completed_quests = _quest_result_round(public_payload, completed_quests)
            lines.append(_quest_result_text(public_payload, completed_quests, prompt_config))
            continue

        if pending_votes:
            lines.extend(_unsettled_vote_lines(pending_votes, prompt_config))
            pending_votes = []
        if pending_quest_submitters:
            lines.append(
                prompt_config.event_templates["quest_submitted"].format(
                    players=_join_or_none(pending_quest_submitters)
                )
            )
            pending_quest_submitters = []

        if event_type == "team_proposed":
            round_number = completed_quests + 1
            leader = _payload_text(public_payload, "leader_player_id", "leader")
            team = _join_or_none(_payload_list(public_payload, "team"))
            lines.append(
                prompt_config.event_templates["team_proposed"].format(
                    round_number=round_number,
                    leader=leader,
                    team=team,
                )
            )
        elif event_type == "speech":
            player = _payload_text(public_payload, "player_id")
            message = _payload_text(public_payload, "message")
            lines.append(
                prompt_config.event_templates["speech"].format(
                    player_id=player,
                    message=message,
                )
            )
        elif event_type == "assassination":
            assassin = _payload_text(public_payload, "assassin_player_id")
            target = _payload_text(public_payload, "target_player_id")
            winner = _payload_text(public_payload, "winner")
            template_key = "assassination_with_winner" if winner else "assassination"
            lines.append(
                prompt_config.event_templates[template_key].format(
                    assassin=assassin,
                    target=target,
                    winner=winner,
                )
            )
        else:
            lines.append(
                prompt_config.event_templates["unknown_event"].format(
                    event_type=event_type,
                    payload=_plain_payload_text(public_payload),
                )
            )

    if pending_votes:
        lines.extend(_unsettled_vote_lines(pending_votes, prompt_config))
    if pending_quest_submitters:
        lines.append(
            prompt_config.event_templates["quest_submitted"].format(
                players=_join_or_none(pending_quest_submitters)
            )
        )

    return lines


def _vote_result_text(
    payload: dict[str, Any],
    votes: list[tuple[str, str]],
    prompt_config: PromptTemplateConfig,
) -> str:
    approved = "通过" if payload.get("approved") is True else "未通过"
    approvals = [player_id for player_id, vote in votes if vote == "approve"]
    rejections = [player_id for player_id, vote in votes if vote == "reject"]
    if approvals or rejections:
        return prompt_config.event_templates["vote_result_with_votes"].format(
            approved_text=approved,
            approvals=_join_or_none(approvals),
            rejections=_join_or_none(rejections),
        )
    return prompt_config.event_templates["vote_result_without_votes"].format(
        approved_text=approved
    )


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
        return prompt_config.event_templates["quest_result_with_cards"].format(
            round_number=round_number,
            result_text=result_text,
            success_cards=success_cards,
            fail_cards=fail_cards,
        )
    return prompt_config.event_templates["quest_result_without_cards"].format(
        round_number=round_number,
        result_text=result_text,
    )


def _role_gameplay_text(role: object, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.role_gameplay.get(
        str(role),
        prompt_config.role_gameplay.get("default", "按当前身份目标行动。"),
    )


def _role_strategy_text(role: object, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.role_strategy_tips.get(
        str(role),
        prompt_config.role_strategy_tips.get("default", "结合公开记录和身份目标行动。"),
    )


def _role_detail_tip_lines(role: object, prompt_config: PromptTemplateConfig) -> list[str]:
    detail_config = prompt_config.role_tip_detail
    if not detail_config.get("enabled"):
        return []
    role_tips = detail_config.get("role_tips", {})
    if not isinstance(role_tips, dict):
        return []
    tips = role_tips.get(str(role), [])
    if not isinstance(tips, list) or not tips:
        return []
    label = str(detail_config.get("label", "详细身份提示"))
    return [f"{label}：", *(f"- {tip}" for tip in tips if str(tip))]


def _extra_information_text(
    context: AiContext,
    prompt_config: PromptTemplateConfig,
) -> str:
    role = str(context.private_view["viewer_role"])
    known_evil_ids = context.private_view.get("known_evil_player_ids", [])
    merlin_candidate_ids = context.private_view.get("merlin_candidate_player_ids", [])
    known_good_ids = context.private_view.get("known_good_player_ids", [])
    if role == "merlin" and known_evil_ids:
        return prompt_config.extra_information["merlin_known_evil"].format(
            players=_join_or_none(known_evil_ids)
        )
    if role == "percival" and merlin_candidate_ids:
        return prompt_config.extra_information["percival_merlin_candidates"].format(
            players=_join_or_none(merlin_candidate_ids)
        )
    if known_good_ids:
        return prompt_config.extra_information["known_good"].format(
            players=_join_or_none(known_good_ids)
        )
    if context.private_view.get("viewer_faction") == "evil" and known_evil_ids:
        return prompt_config.extra_information["evil_teammates"].format(
            players=_join_or_none(known_evil_ids)
        )
    return prompt_config.extra_information["none"]


def _player_ids_text(players: object) -> str:
    if not isinstance(players, list):
        return "无"
    parts = [str(player.get("id", "")) for player in players if isinstance(player, dict)]
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
