from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.game.models import GameOption
from backend.app.services.game_service import GameService
from backend.app.storage.database import initialize_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DS-backed SoloAvalon prompt samples.")
    parser.add_argument("--profile", default="DS")
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--max-human-actions", type=int, default=10)
    parser.add_argument("--max-decisions", type=int, default=24)
    parser.add_argument("--lady", action="store_true")
    parser.add_argument("--tristan", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "prompt_iteration_sample.json")
    args = parser.parse_args()

    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    initialize_database(connection)

    options: set[GameOption] = set()
    if args.lady:
        options.add(GameOption.LADY_OF_LAKE)
    if args.tristan:
        options.add(GameOption.TRISTAN_ISOLDE)

    service = GameService(connection)
    created = service.create_game(
        seed=args.seed,
        player_count=args.players,
        enabled_options=options,
        human_name="真人托管",
        default_llm_profile_id=args.profile,
        auto_advance=False,
    )
    game_id = created["id"]
    state = service.advance_game(game_id)
    human_actions = 0
    errors: list[str] = []
    stop_reason = ""

    _write_sample(service, game_id, args.output, errors, stop_reason)
    while (
        state.get("winner") is None
        and state.get("status") != "error_paused"
        and _decision_count(service, game_id) < args.max_decisions
    ):
        action = state.get("next_human_action")
        if not action:
            state = service.advance_game(game_id)
            _write_sample(service, game_id, args.output, errors, stop_reason)
            continue
        human_actions += 1
        if human_actions > args.max_human_actions:
            stop_reason = f"stopped after {args.max_human_actions} human AI actions"
            break
        try:
            state = service.submit_human_ai_action(game_id)
            _write_sample(service, game_id, args.output, errors, stop_reason)
        except Exception as exc:  # pragma: no cover - diagnostic script.
            errors.append(f"{type(exc).__name__}: {exc}")
            _write_sample(service, game_id, args.output, errors, stop_reason)
            break

    if _decision_count(service, game_id) >= args.max_decisions:
        stop_reason = f"stopped after {args.max_decisions} AI decisions"

    sample = _write_sample(service, game_id, args.output, errors, stop_reason)
    print(json.dumps(_summary(sample, args.output), ensure_ascii=False, indent=2))
    return 0 if not errors and state.get("status") != "error_paused" else 1


def _write_sample(
    service: GameService,
    game_id: str,
    output_path: Path,
    errors: list[str],
    stop_reason: str,
) -> dict[str, Any]:
    room = service.get_room_detail(game_id)
    exported = service.export_game_log(game_id, include_private=True)
    sample = {
        "game": room["game"],
        "players": _players(room["game"]),
        "public_timeline": _public_timeline(exported),
        "speeches": _speeches(exported),
        "team_messages": _team_messages(room["ai_decisions"]),
        "decisions": _decisions(room["ai_decisions"]),
        "usage_by_model": room["usage_by_model"],
        "errors": list(errors),
        "stop_reason": stop_reason,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    return sample


def _decision_count(service: GameService, game_id: str) -> int:
    return len(service.get_room_detail(game_id)["ai_decisions"])


def _players(game: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": player["id"],
            "name": player["name"],
            "revealed_role": player.get("revealed_role"),
            "is_human": player["is_human"],
        }
        for player in game["players"]
    ]


def _public_timeline(exported: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = []
    for event in exported["events"]:
        event_type = event["event_type"]
        payload = event["public_payload"] or {}
        if event_type in {
            "game_created",
            "team_proposed",
            "speech",
            "vote_result",
            "quest_result",
            "lady_of_lake_used",
            "assassination",
        }:
            timeline.append(
                {
                    "event_index": event["event_index"],
                    "event_type": event_type,
                    "payload": payload,
                }
            )
    return timeline


def _speeches(exported: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for event in exported["events"]:
        if event["event_type"] != "speech":
            continue
        payload = event["public_payload"] or {}
        rows.append(
            {
                "player_id": str(payload.get("player_id", "")),
                "message": str(payload.get("message", "")),
            }
        )
    return rows


def _team_messages(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for decision in decisions:
        if decision["decision_type"] != "team_proposal":
            continue
        output = _json_object(decision.get("output"))
        rows.append(
            {
                "player_id": decision["player_id"],
                "team": output.get("team"),
                "public_message": output.get("public_message"),
                "private_reason_summary": output.get("private_reason_summary"),
            }
        )
    return rows


def _decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for decision in decisions:
        output = _json_object(decision.get("output"))
        rows.append(
            {
                "player_id": decision["player_id"],
                "phase": decision["phase"],
                "decision_type": decision["decision_type"],
                "validation_status": decision["validation_status"],
                "strategy_summary": decision["strategy_summary"],
                "output": output,
                "prompt_template_version": decision["prompt_template_version"],
                "total_tokens": decision.get("total_tokens"),
                "cache_hit_rate": decision.get("cache_hit_rate"),
            }
        )
    return rows


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _summary(sample: dict[str, Any], output_path: Path) -> dict[str, Any]:
    return {
        "output": str(output_path),
        "winner": sample["game"].get("winner"),
        "status": sample["game"].get("status"),
        "player_count": sample["game"].get("player_count"),
        "speech_count": len(sample["speeches"]),
        "decision_count": len(sample["decisions"]),
        "usage_by_model": sample["usage_by_model"],
        "errors": sample["errors"],
        "stop_reason": sample["stop_reason"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
