from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from backend.app.game.models import GameState, Phase, Role
from backend.app.game.rules import STANDARD_FACTION_COUNTS, private_view_for_player
from backend.app.prompting.config import PromptTemplateConfig, load_prompt_template_config


CONTEXT_BUILDER_VERSION = "context-builder.v1"

ROLE_DISPLAY_ORDER = (
    Role.MERLIN,
    Role.PERCIVAL,
    Role.LOYAL_SERVANT,
    Role.TRISTAN,
    Role.ISOLDE,
    Role.ASSASSIN,
    Role.MORGANA,
    Role.MORDRED,
    Role.OBERON,
    Role.MINION,
)


@dataclass(frozen=True)
class AiContext:
    viewer_player_id: str
    phase: Phase
    stable_prefix: str
    stable_prefix_hash: str
    dynamic_private_suffix: str
    private_view: dict[str, Any]
    public_state: dict[str, Any]
    recent_public_events: list[dict[str, Any]]
    legal_actions: dict[str, Any]
    context_summary: str
    context_truncated: bool
    context_builder_version: str = CONTEXT_BUILDER_VERSION


class ContextBuilder:
    def __init__(
        self,
        max_recent_events: int | None = None,
        prompt_config: PromptTemplateConfig | None = None,
    ):
        self.max_recent_events = max_recent_events
        self._prompt_config = prompt_config or load_prompt_template_config()

    def build(
        self,
        state: GameState,
        viewer_player_id: str,
        phase: Phase,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiContext:
        view = private_view_for_player(state, viewer_player_id)
        viewer = next(player for player in state.players if player.id == viewer_player_id)
        recent_events = list(public_events or [])
        truncated = self.max_recent_events is not None and len(recent_events) > self.max_recent_events
        if truncated:
            recent_events = recent_events[-self.max_recent_events :]

        private_view = {
            "viewer_player_id": viewer.id,
            "viewer_role": viewer.role.value,
            "viewer_faction": viewer.faction.value,
            "known_evil_player_ids": view.known_evil_player_ids,
            "visible_roles": {
                player_id: _role_value(role)
                for player_id, role in view.visible_roles.items()
            },
        }
        if view.merlin_candidate_player_ids:
            private_view["merlin_candidate_player_ids"] = view.merlin_candidate_player_ids
        if view.known_good_player_ids:
            private_view["known_good_player_ids"] = view.known_good_player_ids
        public_state = {
            "players": [
                {
                    "id": player.id,
                    "seat_index": player.seat_index,
                    "name": player.name,
                    "is_human": player.is_human,
                }
                for player in state.players
            ],
            "current_round": state.current_round,
            "phase": state.phase.value,
            "leader_player_id": state.players[state.leader_index].id,
            "proposed_team": list(state.proposed_team),
            "speech_order": list(state.speech_order),
            "speeches": dict(state.speeches),
            "votes_cast_count": len(state.votes),
            "quest_results": ["success" if result else "fail" for result in state.quest_results],
            "failed_team_votes": state.failed_team_votes,
            "forced_team": state.forced_team,
        }
        legal_actions = _legal_actions(state, viewer_player_id, phase)
        dynamic_payload = {
            "private_view": private_view,
            "public_state": public_state,
            "recent_public_events": recent_events,
            "legal_actions": legal_actions,
        }
        dynamic_suffix = json.dumps(
            dynamic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        stable_prefix = _stable_prefix_for_state(state, self._prompt_config)
        context_summary = (
            f"phase={state.phase.value};round={state.current_round};"
            f"viewer={viewer.id};events={len(recent_events)}"
        )
        return AiContext(
            viewer_player_id=viewer.id,
            phase=phase,
            stable_prefix=stable_prefix,
            stable_prefix_hash=hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest(),
            dynamic_private_suffix=dynamic_suffix,
            private_view=private_view,
            public_state=public_state,
            recent_public_events=recent_events,
            legal_actions=legal_actions,
            context_summary=context_summary,
            context_truncated=truncated,
        )


def _role_value(role: Role | None) -> str | None:
    return role.value if role is not None else None


def _stable_prefix_for_state(state: GameState, prompt_config: PromptTemplateConfig) -> str:
    role_counts = Counter(player.role for player in state.players)
    good_count, evil_count = _faction_counts_for_state(state)
    return "\n".join(
        [
            prompt_config.section_titles["system"],
            *prompt_config.system_lines,
            "",
            prompt_config.section_titles["game_config"],
            prompt_config.labels["player_count"].format(count=len(state.players)),
            prompt_config.labels["faction_counts"].format(
                good_count=good_count,
                evil_count=evil_count,
            ),
            prompt_config.labels["mission_config_header"],
            *_mission_config_lines(state, prompt_config),
            "",
            prompt_config.labels["role_config_header"],
            *_role_description_lines(role_counts, prompt_config),
            "",
            *_optional_mechanic_section_lines(state, prompt_config),
        ]
    )


def _faction_counts_for_state(state: GameState) -> tuple[int, int]:
    configured_counts = STANDARD_FACTION_COUNTS.get(len(state.players))
    if configured_counts is not None:
        return configured_counts
    good_count = sum(1 for player in state.players if player.faction.value == "good")
    return good_count, len(state.players) - good_count


def _mission_config_lines(state: GameState, prompt_config: PromptTemplateConfig) -> list[str]:
    return [
        prompt_config.labels["mission_config_line"].format(
            round_number=mission.round_number,
            team_size=mission.team_size,
            fail_cards_required=mission.fail_cards_required,
        )
        for mission in state.missions
    ]


def _role_description_lines(
    role_counts: Counter[Role],
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    return [
        prompt_config.labels["role_config_line"].format(
            role_label=_role_label(role, prompt_config),
            count=count,
            description=prompt_config.role_descriptions.get(
                role.value,
                prompt_config.role_gameplay.get("default", "按当前游戏规则行动。"),
            ),
        )
        for role, count in _roles_in_display_order(role_counts)
    ]


def _roles_in_display_order(role_counts: Counter[Role]) -> list[tuple[Role, int]]:
    ordered = [
        (role, role_counts[role])
        for role in ROLE_DISPLAY_ORDER
        if role_counts[role] > 0
    ]
    ordered_roles = {role for role, _count in ordered}
    ordered.extend(
        sorted(
            (
                (role, count)
                for role, count in role_counts.items()
                if role not in ordered_roles
            ),
            key=lambda item: item[0].value,
        )
    )
    return ordered


def _optional_mechanic_section_lines(
    state: GameState,
    prompt_config: PromptTemplateConfig,
) -> list[str]:
    enabled_mechanics = [
        mechanic
        for mechanic in prompt_config.optional_mechanics.values()
        if _optional_mechanic_enabled(mechanic, len(state.players))
    ]
    if not enabled_mechanics:
        return []
    lines = [
        prompt_config.labels["optional_mechanics_header"],
    ]
    lines.extend(
        prompt_config.labels["optional_mechanics_line"].format(
            mechanic_label=mechanic["label"],
            description=mechanic["description"],
        )
        for mechanic in enabled_mechanics
    )
    return lines


def _optional_mechanic_enabled(mechanic: dict[str, Any], player_count: int) -> bool:
    return bool(mechanic.get("enabled")) or player_count in mechanic.get(
        "default_enabled_for_player_counts",
        [],
    )


def _role_label(role: Role, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.role_labels.get(role.value, role.value)


def _faction_label(faction: str, prompt_config: PromptTemplateConfig) -> str:
    return prompt_config.faction_labels.get(faction, faction)


def _role_ids_text(role_ids: list[str], prompt_config: PromptTemplateConfig) -> str:
    labels = [prompt_config.role_labels.get(role_id, role_id) for role_id in role_ids]
    return "、".join(labels) if labels else "无"


def _legal_actions(state: GameState, player_id: str, phase: Phase) -> dict[str, Any]:
    mission = state.missions[state.current_round - 1]
    if phase == Phase.TEAM_PROPOSAL:
        return {
            "action": "propose_team",
            "team_size": mission.team_size,
            "player_ids": [player.id for player in state.players],
        }
    if phase == Phase.SPEECH:
        return {"action": "speak", "stances": ["support_team", "oppose_team", "uncertain"]}
    if phase == Phase.VOTING:
        return {"action": "vote", "votes": ["approve", "reject"]}
    if phase == Phase.QUEST:
        player = next(player for player in state.players if player.id == player_id)
        actions = ["success", "fail"] if player.faction.value == "evil" else ["success"]
        return {"action": "mission_action", "mission_actions": actions}
    if phase == Phase.ASSASSINATION:
        return {
            "action": "assassinate",
            "target_player_ids": [player.id for player in state.players if player.id != player_id],
        }
    return {"action": "none"}
