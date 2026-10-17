from __future__ import annotations

from backend.app.game.models import GameState, Phase, Role
from backend.app.storage.ai_decision_repository import AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository
from backend.app.storage.database import Database
from backend.app.storage.event_store import EventStore
from backend.app.storage.game_repository import GameRepository
from .game_flow import GameCommitter, GameReplayError, GameStateLoader


class GameConflict(ValueError):
    def __init__(self, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.current_revision = current_revision


class GameUnitOfWork:
    def __init__(self, database: Database, *, write: bool = False):
        self.database = database
        self.write = write

    def __enter__(self):
        self._scope = self.database.connection(write=self.write)
        self.connection = self._scope.__enter__()
        self.games = GameRepository(self.connection, autocommit=False)
        self.events = EventStore(self.connection, autocommit=False)
        self.ai_decisions = AiDecisionRepository(self.connection, autocommit=False)
        self.ai_memory = AiMemoryRepository(self.connection, autocommit=False)
        self.committer = GameCommitter(
            self.connection, self.games, self.events, self.ai_decisions, self.ai_memory,
            {}, manage_transaction=False,
        )
        return self

    def __exit__(self, *args):
        return self._scope.__exit__(*args)

    def summary(self, game_id):
        summary = self.games.get_game_summary(game_id)
        if summary is None:
            raise ValueError(f"unknown game id: {game_id}")
        return summary

    def load(self, game_id):
        summary = self.summary(game_id)
        loaded = GameStateLoader(self.games, self.events).load(game_id)
        if loaded.replay_error is not None:
            raise GameReplayError(
                game_id, last_replayed_event_index=loaded.last_replayed_event_index,
                failed_event_index=loaded.failed_event_index or 0, cause=loaded.replay_error,
            )
        return loaded.state, summary

    def playable(self, game_id, expected_revision=None):
        state, summary = self.load(game_id)
        if expected_revision is not None and summary.revision != expected_revision:
            raise GameConflict("对局已更新，请使用当前状态重新操作", summary.revision)
        if summary.archived_at is not None:
            raise ValueError("archived game cannot be played")
        return state, summary

    def save_step(self, game_id, before, step):
        self.committer.commit_step(game_id, before, step)
        self.games.update_runtime(game_id, advance_mode=advance_mode(step.state_after))


def human_player(state):
    return next(player for player in state.players if player.is_human)


def next_human_action(state: GameState) -> str | None:
    human = human_player(state)
    if state.phase == Phase.TEAM_PROPOSAL and state.players[state.leader_index].id == human.id:
        return "propose_team"
    if state.phase == Phase.SPEECH and len(state.speeches) < len(state.speech_order):
        if state.speech_order[len(state.speeches)] == human.id:
            return "speak"
    if state.phase == Phase.VOTING and human.id not in state.votes:
        return "vote"
    if state.phase == Phase.QUEST and human.id in state.proposed_team and human.id not in state.quest_actions:
        return "mission_action"
    if state.phase == Phase.ASSASSINATION and human.role == Role.ASSASSIN:
        return "assassinate"
    if state.phase == Phase.LADY_OF_LAKE and state.lady_of_lake_holder_player_id == human.id:
        return "use_lady_of_lake"
    return None


def advance_mode(state):
    return "auto" if state.phase != Phase.COMPLETE and next_human_action(state) is None else None
