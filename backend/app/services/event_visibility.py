from __future__ import annotations

import re
from typing import Any

from backend.app.storage.event_store import EventRecord


# Audit rows and unknown fields never cross the play boundary.
PUBLIC_FIELDS = {
    "game_created": ("game_id", "player_count", "enabled_options", "missions", "current_round", "current_phase"),
    "roles_assigned": ("player_count",),
    "team_proposed": ("leader_player_id", "team"),
    "speech": ("player_id", "message"),
    "vote_cast": ("player_id",),
    "vote_result": ("approved", "failed_team_votes", "votes"),
    "quest_action_submitted": ("player_id",),
    "quest_result": ("quest_results", "success_cards", "fail_cards", "phase", "winner"),
    "lady_of_lake_used": ("viewer_player_id", "target_player_id", "next_holder_player_id", "round_number"),
    "assassination": ("assassin_player_id", "target_player_id", "winner"),
}


def public_event_dicts(events: list[EventRecord]) -> list[dict[str, Any]]:
    payloads = []
    votes = {}
    for event in events:
        if event.event_type not in PUBLIC_FIELDS:
            continue
        if event.event_type == "team_proposed":
            votes = {}
        if event.event_type == "vote_cast":
            votes[event.public_payload["player_id"]] = event.public_payload.get("vote")
        public = normalize_public_payload(event.event_type, event.public_payload)
        if event.event_type == "vote_result":
            public["votes"] = dict(event.public_payload.get("votes", votes))
            votes = {}
        index = len(payloads) + 1
        payloads.append({
            "id": index, "game_id": event.game_id, "event_index": index,
            "event_type": event.event_type, "public_payload": public, "created_at": event.created_at,
        })
    return payloads


def personal_reason_records(
    events: list[EventRecord], player_id: str
) -> list[dict[str, Any]]:
    records = []
    public_count = 0
    placeholders = {
        "模型未提供私有理由摘要。",
        "模型直接输出中文发言。",
        "模型直接输出中文投票。",
    }
    for event in events:
        if event.event_type in PUBLIC_FIELDS:
            public_count += 1
        if event.event_type != "ai_decision":
            continue
        payload = event.public_payload
        if (
            payload.get("player_id") != player_id
            or payload.get("validation_status") != "valid"
        ):
            continue
        summary = payload.get("strategy_summary")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or summary.strip() in placeholders
        ):
            continue
        records.append(
            {
                "player_id": player_id,
                "decision_type": payload.get("decision_type", ""),
                "reason_summary": summary,
                # Public numbering omits audit rows; anchor without renumbering it.
                "after_event_index": public_count,
            }
        )
    return records


def normalize_public_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: payload[field] for field in PUBLIC_FIELDS.get(event_type, ()) if field in payload}
    if event_type == "speech" and "message" in normalized:
        normalized["message"] = normalize_public_player_references(str(normalized["message"]))
    return normalized


def normalize_public_player_references(text: str) -> str:
    return re.sub(r"(?<![A-Za-z0-9_])player_(\d+)(?![A-Za-z0-9_])", r"玩家\1", text)
