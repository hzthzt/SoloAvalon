from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from backend.app.game.events import build_private_view_payloads
from backend.app.game.models import GameState
from backend.app.game.rules import eligible_lady_of_lake_target_ids
from backend.app.storage.database import Database
from .event_visibility import normalize_public_player_references, public_event_dicts
from .game_work import GameUnitOfWork, next_human_action, human_player as _human_player, GameConflict


SUMMARY_FIELDS = ("id", "display_name", "status", "player_count", "enabled_options",
                  "current_round", "current_phase", "winner", "archived_at", "created_at",
                  "updated_at", "revision")


def summary_view(summary):
    values = asdict(summary)
    return {field: values[field] for field in SUMMARY_FIELDS}


class GameQueries:
    def __init__(self, database: Database):
        self.database = database

    def _query(self, call: Callable):
        with GameUnitOfWork(self.database) as work:
            return call(_Projection(work))

    def get_game(self, game_id: str):
        return self._query(lambda query: query.get_game(game_id))

    def list_games(self):
        return self._query(lambda query: [summary_view(game) for game in query.games.list_games()])

    def get_public_events(self, game_id: str, after: int = 0):
        return self._query(lambda query: query.public_events(game_id, after))

    def get_private_events(self, game_id: str, after: int = 0):
        return self._query(lambda query: query.private_events(game_id, after))

    def get_completed_review(self, game_id: str):
        return self._query(lambda query: query.review(game_id))

    def export_game(self, game_id: str, include_private: bool = False):
        def export(query):
            game = query.get_game(game_id)
            events = query.private_events(game_id) if include_private else query.public_events(game_id)
            return {"game": game, "events": events}
        return self._query(export)


class _Projection:
    def __init__(self, work):
        self.work = work
        self.games = work.games
        self.events = work.events

    def get_game(self, game_id):
        return self._public_state(game_id, self.work.load(game_id)[0])

    def require_complete(self, game_id):
        state, _ = self.work.load(game_id)
        if state.winner is None:
            raise GameConflict("完整审计仅在对局完成后提供")

    def public_events(self, game_id, after=0):
        self.work.summary(game_id)
        return [event for event in public_event_dicts(self.events.list_events(game_id))
                if event["event_index"] > after]

    def private_events(self, game_id, after=0):
        self.require_complete(game_id)
        return [asdict(event) for event in self.events.list_events_after(game_id, after)]

    def review(self, game_id):
        self.require_complete(game_id)
        decisions = [asdict(decision) for decision in self.work.ai_decisions.list_decisions(game_id)]
        players = {player.id: player.name for player in self.games.list_players(game_id)}
        return {"game": self.get_game(game_id), "events": self.private_events(game_id),
                "ai_decisions": decisions, "usage_by_player": _usage_by_player(decisions, players),
                "usage_by_model": _usage_by_model(decisions)}

    def _public_state(self, game_id: str, state: GameState) -> dict[str, Any]:
        summary = self.games.get_game_summary(game_id)
        human = _human_player(state)
        view_payload = build_private_view_payloads(state, human.id)[1]
        visible_roles = view_payload["visible_roles"]
        return {
            "id": game_id,
            "display_name": summary.display_name if summary else game_id,
            "revision": summary.revision,
            "is_advancing": summary.status == "active" and summary.archived_at is None and summary.advance_mode is not None,
            "status": summary.status if summary else ("complete" if state.winner else "active"),
            "player_count": len(state.players),
            "phase": state.phase.value,
            "current_round": state.current_round,
            "leader_player_id": state.players[state.leader_index].id,
            "missions": [
                {
                    "round_number": mission.round_number,
                    "team_size": mission.team_size,
                    "fail_cards_required": mission.fail_cards_required,
                }
                for mission in state.missions
            ],
            "enabled_options": sorted(option.value for option in state.enabled_options),
            "human_player_id": human.id,
            "human_role": human.role.value,
            "human_faction": human.faction.value,
            "known_evil_player_ids": view_payload["known_evil_player_ids"],
            "lady_of_lake_holder_player_id": state.lady_of_lake_holder_player_id,
            "lady_of_lake_previous_holder_ids": list(state.lady_of_lake_previous_holder_ids),
            "lady_of_lake_eligible_target_ids": list(
                eligible_lady_of_lake_target_ids(state)
            ),
            "lady_of_lake_known_factions": view_payload["lady_of_lake_known_factions"],
            "players": [
                {
                    "id": player.id,
                    "seat_index": player.seat_index,
                    "name": player.name,
                    "original_name": player.original_name,
                    "is_human": player.is_human,
                    "visible_role": visible_roles[player.id],
                    "revealed_role": player.role.value if state.winner else None,
                }
                for player in state.players
            ],
            "proposed_team": list(state.proposed_team),
            "speech_order": list(state.speech_order),
            "speeches": {
                player_id: normalize_public_player_references(message)
                for player_id, message in state.speeches.items()
            },
            "votes_cast_count": len(state.votes),
            "quest_actions_submitted_count": len(state.quest_actions),
            "quest_results": ["success" if result else "fail" for result in state.quest_results],
            "failed_team_votes": state.failed_team_votes,
            "forced_team": state.forced_team,
            "winner": state.winner.value if state.winner else None,
            "assassination_target_id": state.assassination_target_id,
            "next_human_action": next_human_action(state),
            "events": public_event_dicts(self.events.list_events(game_id)),
        }



def _usage_by_player(
    decisions: list[dict[str, Any]],
    players: dict[str, str],
) -> list[dict[str, Any]]:
    summaries = _usage_groups(decisions, lambda decision: str(decision["player_id"]))
    return [
        {
            **summary,
            "player_id": key,
            "player_name": players.get(key, key),
        }
        for key, summary in summaries.items()
    ]


def _usage_by_model(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = _usage_groups(decisions, lambda decision: str(decision["model_name"]))
    return [
        {
            **summary,
            "model_name": key,
        }
        for key, summary in summaries.items()
    ]


def _usage_groups(
    decisions: list[dict[str, Any]],
    key_for: Callable[[dict[str, Any]], str],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    hit_rates: dict[str, list[float]] = {}
    for decision in decisions:
        key = key_for(decision)
        if key not in groups:
            groups[key] = {
                "decision_count": 0,
                "total_tokens": 0,
                "average_cache_hit_rate": None,
            }
            hit_rates[key] = []
        groups[key]["decision_count"] += 1
        total_tokens = decision.get("total_tokens")
        if isinstance(total_tokens, int):
            groups[key]["total_tokens"] += total_tokens
        cache_hit_rate = decision.get("cache_hit_rate")
        if isinstance(cache_hit_rate, (int, float)):
            hit_rates[key].append(float(cache_hit_rate))
    for key, rates in hit_rates.items():
        if rates:
            groups[key]["average_cache_hit_rate"] = sum(rates) / len(rates)
    return groups
