from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from backend.app.game.models import GameState, Phase, Role
from backend.app.game.rules import private_view_for_player


CONTEXT_BUILDER_VERSION = "context-builder.v1"

STABLE_PREFIX = """SoloAvalon Avalon rules v1.
There are five players, three good players, and two evil players.
The good side wins after three successful quests if the assassin misses Merlin.
The evil side wins after three failed quests, or if the assassin kills Merlin.
Only legal JSON matching the current phase contract is accepted.
Do not reveal private identity knowledge in public messages.
"""


@dataclass(frozen=True)
class AiContext:
    viewer_player_id: str
    phase: Phase
    stable_prefix: str
    stable_prefix_hash: str
    dynamic_private_suffix: str
    private_view: dict[str, Any]
    public_state: dict[str, Any]
    legal_actions: dict[str, Any]
    context_summary: str
    context_truncated: bool
    context_builder_version: str = CONTEXT_BUILDER_VERSION


class ContextBuilder:
    def __init__(self, max_recent_events: int = 20):
        self.max_recent_events = max_recent_events

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
        truncated = len(recent_events) > self.max_recent_events
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
        stable_prefix = STABLE_PREFIX.strip()
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
            legal_actions=legal_actions,
            context_summary=context_summary,
            context_truncated=truncated,
        )


def _role_value(role: Role | None) -> str | None:
    return role.value if role is not None else None


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
