from __future__ import annotations

from dataclasses import replace

from backend.app.game.events import build_game_created_payloads, build_roles_assigned_payloads, build_private_view_payloads
from backend.app.game.models import Phase
from backend.app.game.rules import create_game
from .game_flow import GameStepRunner
from .game_queries import GameQueries, summary_view
from .game_work import GameUnitOfWork, advance_mode, human_player, next_human_action


class GameApplication:
    def __init__(self, database, notifier):
        self.database = database
        self.notifier = notifier
        self.queries = GameQueries(database)
        self.wake = lambda: None

    def changed(self, game_id):
        self.notifier.notify(game_id)
        self.wake()

    def create_game(self, *, seed=None, player_count=5, enabled_options=None, human_name=None,
                    ai_names=None, default_llm_profile_id=None, ai_profile_overrides=None):
        state = create_game(player_count=player_count, seed=seed, enabled_options=enabled_options,
                            human_name=human_name or "真人玩家", ai_names=ai_names or [])
        overrides = ai_profile_overrides or {}
        profiles = iter(overrides.values())
        state = replace(state, players=tuple(
            replace(player, llm_profile_id=next(profiles, None)) if not player.is_human else player
            for player in state.players
        ))
        with GameUnitOfWork(self.database, write=True) as work:
            game_id = work.games.next_game_id()
            work.games.save_new_game(
                state, game_id, work.games.next_room_display_name(), default_llm_profile_id,
            )
            for kind, payloads in [
                ("game_created", build_game_created_payloads(state, game_id)),
                ("roles_assigned", build_roles_assigned_payloads(state)),
                *[("private_view_recorded", build_private_view_payloads(state, player.id)) for player in state.players],
            ]:
                work.events.append_event(game_id, kind, *payloads)
            work.games.update_runtime(game_id, advance_mode=advance_mode(state))
        self.changed(game_id)
        return self.queries.get_game(game_id)

    def submit_human_action(self, game_id, expected_revision, action_type, payload):
        with GameUnitOfWork(self.database, write=True) as work:
            state, _ = work.playable(game_id, expected_revision)
            expected = next_human_action(state)
            if expected is None or action_type != expected:
                raise ValueError(f"expected human action {expected}, got {action_type}")
            step = GameStepRunner().apply_player_action(state, human_player(state).id, action_type, payload)
            work.save_step(game_id, state, step)
        self.changed(game_id)
        return self.queries.get_game(game_id)

    def request_human_ai(self, game_id, expected_revision):
        with GameUnitOfWork(self.database, write=True) as work:
            state, summary = work.playable(game_id, expected_revision)
            if summary.status != "active" or summary.advance_mode is not None:
                raise ValueError("game is paused or already advancing")
            if next_human_action(state) is None:
                raise ValueError("no human action is available for AI control")
            work.games.update_runtime(game_id, advance_mode="human_once")
        self.changed(game_id)
        return self.queries.get_game(game_id)

    def resume_game(self, game_id):
        with GameUnitOfWork(self.database, write=True) as work:
            state, summary = work.playable(game_id)
            if state.phase != Phase.COMPLETE:
                mode = summary.advance_mode or advance_mode(state)
                if summary.status == "error_paused" or mode is not None:
                    work.games.update_runtime(game_id, advance_mode=mode, status="active")
        self.changed(game_id)
        return self.queries.get_game(game_id)

    def archive_game(self, game_id):
        with GameUnitOfWork(self.database, write=True) as work:
            summary = work.summary(game_id)
            if summary.archived_at is None:
                work.games.archive_game(game_id)
                work.games.update_runtime(game_id, advance_mode=None)
            result = summary_view(work.summary(game_id))
        self.changed(game_id)
        return result

    def delete_game(self, game_id):
        with GameUnitOfWork(self.database, write=True) as work:
            work.summary(game_id)
            work.games.delete_game(game_id)
        self.changed(game_id)
