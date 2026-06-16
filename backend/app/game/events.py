from __future__ import annotations

from typing import Any

from .models import GameState, Role
from .rules import private_view_for_player


def build_game_created_payloads(
    state: GameState,
    game_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return (
        {
            "game_id": game_id,
            "player_count": len(state.players),
            "current_round": state.current_round,
            "current_phase": state.phase.value,
        },
        None,
    )


def build_roles_assigned_payloads(
    state: GameState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"player_count": len(state.players)},
        {
            "roles_by_player_id": {
                player.id: player.role.value
                for player in state.players
            },
            "factions_by_player_id": {
                player.id: player.faction.value
                for player in state.players
            },
        },
    )


def build_private_view_payloads(
    state: GameState,
    viewer_player_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    view = private_view_for_player(state, viewer_player_id)
    visible_roles = {
        player_id: _role_value(role)
        for player_id, role in view.visible_roles.items()
    }
    return (
        {"viewer_player_id": viewer_player_id},
        {
            "viewer_player_id": viewer_player_id,
            "known_evil_player_ids": view.known_evil_player_ids,
            "visible_roles": visible_roles,
        },
    )


def _role_value(role: Role | None) -> str | None:
    return role.value if role is not None else None

