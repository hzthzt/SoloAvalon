from __future__ import annotations

import json
import re
from typing import Any

from backend.app.ai.strategy import (
    AssassinationDecision,
    MissionActionDecision,
    SpeechDecision,
    TeamProposalDecision,
    VoteDecision,
)
from backend.app.game.models import Faction, GameState, MissionAction, Vote


def parse_json_object(raw: str) -> dict[str, Any]:
    text = _strip_code_fence(raw)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def team_decision_from_output(output: dict[str, Any], state: GameState) -> TeamProposalDecision:
    team = output.get("team")
    if not isinstance(team, list) or not all(isinstance(item, str) for item in team):
        raise ValueError("team must be a list of player ids")
    required_size = state.missions[state.current_round - 1].team_size
    if len(team) != required_size or len(set(team)) != len(team):
        raise ValueError("team has invalid size or duplicate players")
    valid_ids = {player.id for player in state.players}
    if set(team) - valid_ids:
        raise ValueError("team contains unknown players")
    return TeamProposalDecision(
        team=tuple(team),
        private_reason_summary=_string(output, "private_reason_summary"),
        public_message=_normalize_public_player_references(str(output.get("public_message", ""))),
    )


def speech_decision_from_output(output: dict[str, Any]) -> SpeechDecision:
    stance = str(output.get("stance", "uncertain"))
    if stance not in {"support_team", "oppose_team", "uncertain"}:
        raise ValueError("invalid speech stance")
    return SpeechDecision(
        public_message=_normalize_public_player_references(_string(output, "public_message")),
        stance=stance,
        private_reason_summary=_string(output, "private_reason_summary"),
    )


def speech_decision_from_text(raw: str) -> SpeechDecision:
    text = _extracted_public_message(raw) or _plain_text(raw)
    if _looks_like_json_object(text):
        raise ValueError("invalid speech JSON output")
    text = _normalize_public_player_references(text)
    return SpeechDecision(
        public_message=text,
        stance=_infer_stance(text),
        private_reason_summary="模型直接输出中文发言。",
    )


def vote_decision_from_output(output: dict[str, Any]) -> VoteDecision:
    try:
        vote = Vote(_string(output, "vote"))
    except ValueError as exc:
        raise ValueError("invalid vote") from exc
    return VoteDecision(
        vote=vote,
        private_reason_summary=_string(output, "private_reason_summary"),
        public_reason=str(output.get("public_reason", "")),
    )


def vote_decision_from_text(raw: str) -> VoteDecision:
    text = _plain_text(raw)
    vote = _infer_vote(text)
    return VoteDecision(
        vote=vote,
        private_reason_summary="模型直接输出中文投票。",
        public_reason=text,
    )


def mission_decision_from_output(
    output: dict[str, Any],
    state: GameState,
    player_id: str,
) -> MissionActionDecision:
    try:
        action = MissionAction(_string(output, "mission_action"))
    except ValueError as exc:
        raise ValueError("invalid mission action") from exc
    player = next(player for player in state.players if player.id == player_id)
    if player.faction == Faction.GOOD and action == MissionAction.FAIL:
        raise ValueError("good players cannot submit fail")
    return MissionActionDecision(
        mission_action=action,
        private_reason_summary=_string(output, "private_reason_summary"),
    )


def assassination_decision_from_output(
    output: dict[str, Any],
    state: GameState,
    assassin_player_id: str,
) -> AssassinationDecision:
    target = _string(output, "target_player_id")
    valid_targets = {player.id for player in state.players if player.id != assassin_player_id}
    if target not in valid_targets:
        raise ValueError("invalid assassination target")
    ranking = output.get("candidate_ranking", [])
    if not isinstance(ranking, list) or not all(isinstance(item, str) for item in ranking):
        raise ValueError("candidate_ranking must be a list of player ids")
    return AssassinationDecision(
        target_player_id=target,
        private_reason_summary=_string(output, "private_reason_summary"),
        candidate_ranking=tuple(player_id for player_id in ranking if player_id in valid_targets),
    )


def _string(output: dict[str, Any], key: str) -> str:
    value = output.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _plain_text(raw: str) -> str:
    text = _strip_code_fence(raw)
    if not text:
        raise ValueError("model output text is required")
    return text


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _extracted_public_message(raw: str) -> str | None:
    text = _strip_code_fence(raw)
    match = re.search(r'"public_message"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if not match:
        return None
    encoded = f'"{match.group(1)}"'
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        value = match.group(1).replace('\\"', '"')
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _looks_like_json_object(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or '"private_reason_summary"' in stripped


def _normalize_public_player_references(text: str) -> str:
    return re.sub(r"(?<![A-Za-z0-9_])player_(\d+)(?![A-Za-z0-9_])", r"玩家\1", text)


def _infer_stance(text: str) -> str:
    if _contains_any(text, _reject_markers()):
        return "oppose_team"
    if _contains_any(text, _approve_markers()):
        return "support_team"
    return "uncertain"


def _infer_vote(text: str) -> Vote:
    if _contains_any(text, _reject_markers()):
        return Vote.REJECT
    if _contains_any(text, _approve_markers()):
        return Vote.APPROVE
    raise ValueError("plain vote output must contain approve or reject")


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _reject_markers() -> tuple[str, ...]:
    return (
        "反对",
        "不赞成",
        "不同意",
        "不支持",
        "拒绝",
        "否决",
        "reject",
        "oppose",
        "no",
    )


def _approve_markers() -> tuple[str, ...]:
    return (
        "赞成",
        "同意",
        "支持",
        "通过",
        "approve",
        "accept",
        "yes",
    )
