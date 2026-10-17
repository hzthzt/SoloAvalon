from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.game.models import (
    Faction,
    GameOption,
    GameState,
    MissionAction,
    MissionConfig,
    Phase,
    Player,
    Role,
    Vote,
)
from backend.app.game.rules import (
    STANDARD_MISSION_CONFIGS,
    InvalidActionError,
    assassinate,
    cast_vote,
    finalize_quest,
    finalize_vote,
    propose_team,
    record_speech,
    submit_quest_action,
    use_lady_of_lake,
)
from backend.app.storage.ai_decision_repository import AiDecisionInput, AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository, AiMemorySnapshotInput
from backend.app.storage.event_store import EventRecord, EventStore
from backend.app.storage.game_repository import GameRepository


class GameEventNotifierProtocol(Protocol):
    def notify(self, game_id: str) -> None:
        ...


AUDIT_EVENT_TYPES = {
    "game_created",
    "roles_assigned",
    "private_view_recorded",
    "ai_decision",
    "runner_error",
}

RULE_EVENT_TYPES = {
    "team_proposed",
    "speech",
    "vote_cast",
    "vote_result",
    "quest_action_submitted",
    "quest_result",
    "lady_of_lake_used",
    "assassination",
}


@dataclass(frozen=True)
class PendingEvent:
    event_type: str
    public_payload: dict[str, Any]
    private_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class StepResult:
    state_after: GameState
    rule_events: tuple[PendingEvent, ...]
    audit_events: tuple[PendingEvent, ...] = ()
    ai_decision: AiDecisionInput | None = None
    ai_memory: AiMemorySnapshotInput | None = None


@dataclass(frozen=True)
class LoadedGameState:
    state: GameState
    last_replayed_event_index: int
    replay_error: Exception | None = None
    failed_event_index: int | None = None


class GameReplayError(ValueError):
    def __init__(
        self,
        game_id: str,
        *,
        last_replayed_event_index: int,
        failed_event_index: int,
        cause: Exception,
    ):
        self.game_id = game_id
        self.last_replayed_event_index = last_replayed_event_index
        self.failed_event_index = failed_event_index
        self.cause = cause
        super().__init__(
            "game event replay failed for "
            f"{game_id}: last_replayed_event_index={last_replayed_event_index}, "
            f"failed_event_index={failed_event_index}, cause={cause}"
        )


class GameStateLoader:
    def __init__(self, games: GameRepository, events: EventStore):
        self._games = games
        self._events = events

    def load(self, game_id: str) -> LoadedGameState:
        summary = self._games.get_game_summary(game_id)
        if summary is None:
            raise ValueError(f"unknown active game id: {game_id}")

        players = tuple(
            Player(
                id=player.id,
                seat_index=player.seat_index,
                name=player.name,
                is_human=player.is_human,
                role=Role(player.role),
                faction=Faction(player.faction),
                original_name=player.original_name,
                llm_profile_id=player.llm_profile_id,
            )
            for player in self._games.list_players(game_id)
        )
        if not players:
            raise ValueError(f"game has no persisted players: {game_id}")

        enabled_options = frozenset(GameOption(option) for option in summary.enabled_options)
        lake_holder = None
        lake_previous_holders: tuple[str, ...] = ()
        if GameOption.LADY_OF_LAKE in enabled_options:
            lake_holder = f"player_{summary.player_count}"
            lake_previous_holders = (lake_holder,)
        history = self._events.list_events(game_id)
        created = next((event for event in history if event.event_type == "game_created"), None)
        missions = STANDARD_MISSION_CONFIGS[summary.player_count]
        initial_error = None
        if created is not None and "missions" in created.public_payload:
            try:
                missions = tuple(MissionConfig(**mission) for mission in created.public_payload["missions"])
                if not missions:
                    raise ValueError("persisted missions must not be empty")
            except (ValueError, KeyError, TypeError) as error:
                missions = STANDARD_MISSION_CONFIGS[summary.player_count]
                initial_error = error
        state = GameState(
            players=players,
            missions=missions,
            enabled_options=enabled_options,
            lady_of_lake_holder_player_id=lake_holder,
            lady_of_lake_previous_holder_ids=lake_previous_holders,
        )
        if initial_error is not None:
            return LoadedGameState(state, 0, initial_error, created.event_index)

        last_replayed_event_index = 0
        for event in history:
            try:
                state = apply_replay_event(state, event)
            except (InvalidActionError, ValueError, KeyError, TypeError) as exc:
                return LoadedGameState(
                    state=state,
                    last_replayed_event_index=last_replayed_event_index,
                    replay_error=exc,
                    failed_event_index=event.event_index,
                )
            if event.event_type in RULE_EVENT_TYPES:
                last_replayed_event_index = event.event_index
        return LoadedGameState(state=state, last_replayed_event_index=last_replayed_event_index)


class GameStepRunner:
    def apply_player_action(
        self,
        state: GameState,
        player_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> StepResult:
        if action_type == "propose_team":
            state_after = propose_team(state, player_id, tuple(payload["team"]))
            return StepResult(
                state_after=state_after,
                rule_events=(
                    PendingEvent(
                        "team_proposed",
                        {
                            "leader_player_id": player_id,
                            "team": list(state_after.proposed_team),
                        },
                    ),
                ),
            )
        if action_type == "speak":
            message = str(payload["message"])
            state_after = record_speech(state, player_id, message)
            return StepResult(
                state_after=state_after,
                rule_events=(PendingEvent("speech", {"player_id": player_id, "message": message}),),
            )
        if action_type == "vote":
            vote = Vote(payload["vote"])
            state_after = cast_vote(state, player_id, vote)
            return StepResult(
                state_after=state_after,
                rule_events=(PendingEvent("vote_cast", {"player_id": player_id, "vote": vote.value}),),
            )
        if action_type == "mission_action":
            action = MissionAction(payload["mission_action"])
            state_after = submit_quest_action(state, player_id, action)
            return StepResult(
                state_after=state_after,
                rule_events=(
                    PendingEvent(
                        "quest_action_submitted",
                        {"player_id": player_id},
                        {"mission_action": action.value},
                    ),
                ),
            )
        if action_type == "assassinate":
            target = str(payload["target_player_id"])
            state_after = assassinate(state, player_id, target)
            return StepResult(
                state_after=state_after,
                rule_events=(
                    PendingEvent(
                        "assassination",
                        {
                            "assassin_player_id": player_id,
                            "target_player_id": target,
                            "winner": state_after.winner.value if state_after.winner else None,
                        },
                    ),
                ),
            )
        if action_type == "use_lady_of_lake":
            target = str(payload["target_player_id"])
            state_after = use_lady_of_lake(state, player_id, target)
            inspection = state_after.lady_of_lake_inspections[-1]
            return StepResult(
                state_after=state_after,
                rule_events=(
                    PendingEvent(
                        "lady_of_lake_used",
                        {
                            "viewer_player_id": player_id,
                            "target_player_id": target,
                            "next_holder_player_id": target,
                            "round_number": inspection.round_number,
                        },
                        {"target_faction": inspection.target_faction.value},
                    ),
                ),
            )
        raise ValueError(f"unknown action type: {action_type}")

    def apply_human_action(
        self,
        game_id: str,
        state: GameState,
        action_type: str,
        payload: dict[str, Any],
        *,
        human_player_id: str,
    ) -> StepResult:
        del game_id
        return self.apply_player_action(state, human_player_id, action_type, payload)

    def finalize_vote(self, state: GameState) -> StepResult:
        state_after = finalize_vote(state)
        return StepResult(
            state_after=state_after,
            rule_events=(
                PendingEvent(
                    "vote_result",
                    {
                        "approved": state_after.phase == Phase.QUEST,
                        "failed_team_votes": state_after.failed_team_votes,
                        "votes": {player_id: vote.value for player_id, vote in state.votes.items()},
                    },
                ),
            ),
        )

    def finalize_quest(self, state: GameState) -> StepResult:
        success_cards = sum(
            1 for action in state.quest_actions.values() if action == MissionAction.SUCCESS
        )
        fail_cards = sum(
            1 for action in state.quest_actions.values() if action == MissionAction.FAIL
        )
        state_after = finalize_quest(state)
        return StepResult(
            state_after=state_after,
            rule_events=(
                PendingEvent(
                    "quest_result",
                    {
                        "quest_results": [
                            "success" if result else "fail"
                            for result in state_after.quest_results
                        ],
                        "success_cards": success_cards,
                        "fail_cards": fail_cards,
                        "phase": state_after.phase.value,
                        "winner": state_after.winner.value if state_after.winner else None,
                    },
                ),
            ),
        )


class GameCommitter:
    def __init__(
        self,
        connection: sqlite3.Connection,
        games: GameRepository,
        events: EventStore,
        ai_decisions: AiDecisionRepository,
        ai_memory: AiMemoryRepository,
        states: dict[str, GameState],
        event_notifier: GameEventNotifierProtocol | None = None,
        manage_transaction: bool = True,
    ):
        self._connection = connection
        self._games = (
            GameRepository(connection, autocommit=False)
            if type(games) is GameRepository
            else games
        )
        self._events = (
            EventStore(connection, autocommit=False)
            if type(events) is EventStore
            else events
        )
        self._ai_decisions = (
            AiDecisionRepository(connection, autocommit=False)
            if type(ai_decisions) is AiDecisionRepository
            else ai_decisions
        )
        self._ai_memory = (
            AiMemoryRepository(connection, autocommit=False)
            if type(ai_memory) is AiMemoryRepository
            else ai_memory
        )
        self._states = states
        self._event_notifier = event_notifier
        self._manage_transaction = manage_transaction

    def commit_step(
        self,
        game_id: str,
        state_before: GameState,
        step_result: StepResult,
    ) -> GameState:
        del state_before
        try:
            if step_result.ai_decision is not None:
                self._ai_decisions.save_decision(step_result.ai_decision)
            if step_result.ai_memory is not None:
                self._ai_memory.save_snapshot(step_result.ai_memory)
            for event in step_result.audit_events:
                self._events.append_event(
                    game_id,
                    event.event_type,
                    event.public_payload,
                    event.private_payload,
                )
            for event in step_result.rule_events:
                self._events.append_event(
                    game_id,
                    event.event_type,
                    event.public_payload,
                    event.private_payload,
                )
            self._games.update_game_state(game_id, step_result.state_after)
            if self._manage_transaction:
                self._connection.commit()
        except Exception:
            if self._manage_transaction:
                self._connection.rollback()
            raise
        if self._manage_transaction:
            self._states[game_id] = step_result.state_after
            self._notify_game_events(game_id)
        return step_result.state_after

    def commit_ai_error(
        self,
        game_id: str,
        ai_decision: AiDecisionInput,
        audit_event: PendingEvent,
    ) -> None:
        try:
            self._ai_decisions.save_decision(ai_decision)
            self._events.append_event(
                game_id,
                audit_event.event_type,
                audit_event.public_payload,
                audit_event.private_payload,
            )
            self._games.update_game_status(game_id, "error_paused")
            if self._manage_transaction:
                self._connection.commit()
        except Exception:
            if self._manage_transaction:
                self._connection.rollback()
            raise
        if self._manage_transaction:
            self._notify_game_events(game_id)

    def _notify_game_events(self, game_id: str) -> None:
        if self._event_notifier is not None:
            self._event_notifier.notify(game_id)


def apply_replay_event(state: GameState, event: EventRecord) -> GameState:
    public_payload = event.public_payload
    private_payload = event.private_payload or {}
    if event.event_type in AUDIT_EVENT_TYPES:
        return state
    if event.event_type == "team_proposed":
        return propose_team(
            state,
            str(public_payload["leader_player_id"]),
            tuple(public_payload["team"]),
        )
    if event.event_type == "speech":
        return record_speech(
            state,
            str(public_payload["player_id"]),
            str(public_payload["message"]),
        )
    if event.event_type == "vote_cast":
        return cast_vote(
            state,
            str(public_payload["player_id"]),
            Vote(public_payload["vote"]),
        )
    if event.event_type == "vote_result":
        return finalize_vote(state)
    if event.event_type == "quest_action_submitted":
        mission_action = private_payload.get("mission_action")
        if mission_action is None:
            raise ValueError("cannot restore quest action without private mission_action")
        return submit_quest_action(
            state,
            str(public_payload["player_id"]),
            MissionAction(mission_action),
        )
    if event.event_type == "quest_result":
        return finalize_quest(state)
    if event.event_type == "lady_of_lake_used":
        target_faction = private_payload.get("target_faction")
        if target_faction is None:
            raise ValueError("cannot restore lady of lake without private target_faction")
        replayed = use_lady_of_lake(
            state,
            str(public_payload["viewer_player_id"]),
            str(public_payload["target_player_id"]),
        )
        inspection = replayed.lady_of_lake_inspections[-1]
        if inspection.target_faction != Faction(target_faction):
            raise ValueError("lady of lake private faction does not match restored player faction")
        return replayed
    if event.event_type == "assassination":
        return assassinate(
            state,
            str(public_payload["assassin_player_id"]),
            str(public_payload["target_player_id"]),
        )
    raise ValueError(f"unsupported game event type: {event.event_type}")


def _human_player(state: GameState) -> Player:
    return next(player for player in state.players if player.is_human)
