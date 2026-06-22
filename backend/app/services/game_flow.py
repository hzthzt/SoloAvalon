from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from backend.app.game.models import (
    Faction,
    GameOption,
    GameState,
    MissionAction,
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


AUDIT_EVENT_TYPES = {
    "game_created",
    "roles_assigned",
    "private_view_recorded",
    "ai_decision",
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
        state = GameState(
            players=players,
            missions=STANDARD_MISSION_CONFIGS[summary.player_count],
            enabled_options=enabled_options,
            lady_of_lake_holder_player_id=lake_holder,
            lady_of_lake_previous_holder_ids=lake_previous_holders,
        )

        last_replayed_event_index = 0
        for event in self._events.list_events(game_id):
            try:
                state = apply_replay_event(state, event)
            except (InvalidActionError, ValueError) as exc:
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
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        self._states[game_id] = step_result.state_after
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
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise


class GameAdvanceLoop:
    def __init__(
        self,
        *,
        ai_player: Callable[[], Any],
        profile_for_ai: Callable[[str, str], Any],
        run_ai_turn: Callable[..., Any],
        attach_ai_decision: Callable[..., StepResult],
        public_events_for_ai: Callable[[str], list[dict[str, Any]]],
        next_human_action: Callable[[GameState], str | None],
        set_state: Callable[[str, GameState], None],
        step_runner: GameStepRunner,
        committer: GameCommitter,
    ):
        self._ai_player = ai_player
        self._profile_for_ai = profile_for_ai
        self._run_ai_turn = run_ai_turn
        self._attach_ai_decision = attach_ai_decision
        self._public_events_for_ai = public_events_for_ai
        self._next_human_action = next_human_action
        self._set_state = set_state
        self._step_runner = step_runner
        self._committer = committer

    def advance_until_blocked(self, game_id: str, state: GameState) -> GameState:
        for _ in range(100):
            human_action = self._next_human_action(state)
            if human_action is not None or state.phase == Phase.COMPLETE:
                self._set_state(game_id, state)
                return state

            if state.phase == Phase.TEAM_PROPOSAL:
                leader = state.players[state.leader_index]
                profile = self._profile_for_ai(game_id, leader.id)
                result = self._run_ai_turn(
                    game_id,
                    leader.id,
                    state.phase,
                    "team_proposal",
                    profile,
                    lambda: self._ai_player().propose_team(
                        state,
                        leader.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                step_result = self._step_runner.apply_player_action(
                    state,
                    leader.id,
                    "propose_team",
                    {"team": result.decision.team},
                )
                step_result = self._attach_ai_decision(
                    step_result,
                    game_id,
                    leader.id,
                    Phase.TEAM_PROPOSAL,
                    "team_proposal",
                    profile,
                    result,
                )
                state = self._committer.commit_step(game_id, state, step_result)
            elif state.phase == Phase.SPEECH:
                next_speaker_id = state.speech_order[len(state.speeches)]
                profile = self._profile_for_ai(game_id, next_speaker_id)
                result = self._run_ai_turn(
                    game_id,
                    next_speaker_id,
                    Phase.SPEECH,
                    "speech",
                    profile,
                    lambda: self._ai_player().speak(
                        state,
                        next_speaker_id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                step_result = self._step_runner.apply_player_action(
                    state,
                    next_speaker_id,
                    "speak",
                    {"message": result.decision.public_message},
                )
                step_result = self._attach_ai_decision(
                    step_result,
                    game_id,
                    next_speaker_id,
                    Phase.SPEECH,
                    "speech",
                    profile,
                    result,
                )
                state = self._committer.commit_step(game_id, state, step_result)
            elif state.phase == Phase.VOTING:
                human = _human_player(state)
                for player in state.players:
                    if player.is_human or player.id in state.votes:
                        continue
                    profile = self._profile_for_ai(game_id, player.id)
                    result = self._run_ai_turn(
                        game_id,
                        player.id,
                        Phase.VOTING,
                        "vote",
                        profile,
                        lambda: self._ai_player().vote(
                            state,
                            player.id,
                            profile,
                            public_events=self._public_events_for_ai(game_id),
                        ),
                    )
                    step_result = self._step_runner.apply_player_action(
                        state,
                        player.id,
                        "vote",
                        {"vote": result.decision.vote.value},
                    )
                    step_result = self._attach_ai_decision(
                        step_result,
                        game_id,
                        player.id,
                        Phase.VOTING,
                        "vote",
                        profile,
                        result,
                    )
                    state = self._committer.commit_step(game_id, state, step_result)
                if human.id not in state.votes:
                    self._set_state(game_id, state)
                    return state
                step_result = self._step_runner.finalize_vote(state)
                state = self._committer.commit_step(game_id, state, step_result)
            elif state.phase == Phase.QUEST:
                human = _human_player(state)
                for player_id in state.proposed_team:
                    if player_id == human.id or player_id in state.quest_actions:
                        continue
                    player = next(player for player in state.players if player.id == player_id)
                    if player.faction == Faction.GOOD:
                        step_result = self._step_runner.apply_player_action(
                            state,
                            player_id,
                            "mission_action",
                            {"mission_action": MissionAction.SUCCESS.value},
                        )
                        state = self._committer.commit_step(game_id, state, step_result)
                        continue
                    profile = self._profile_for_ai(game_id, player_id)
                    result = self._run_ai_turn(
                        game_id,
                        player_id,
                        Phase.QUEST,
                        "mission_action",
                        profile,
                        lambda: self._ai_player().mission_action(
                            state,
                            player_id,
                            profile,
                            public_events=self._public_events_for_ai(game_id),
                        ),
                    )
                    step_result = self._step_runner.apply_player_action(
                        state,
                        player_id,
                        "mission_action",
                        {"mission_action": result.decision.mission_action.value},
                    )
                    step_result = self._attach_ai_decision(
                        step_result,
                        game_id,
                        player_id,
                        Phase.QUEST,
                        "mission_action",
                        profile,
                        result,
                    )
                    state = self._committer.commit_step(game_id, state, step_result)
                if human.id in state.proposed_team and human.id not in state.quest_actions:
                    self._set_state(game_id, state)
                    return state
                step_result = self._step_runner.finalize_quest(state)
                state = self._committer.commit_step(game_id, state, step_result)
            elif state.phase == Phase.ASSASSINATION:
                assassin = next(player for player in state.players if player.role.value == "assassin")
                profile = self._profile_for_ai(game_id, assassin.id)
                result = self._run_ai_turn(
                    game_id,
                    assassin.id,
                    Phase.ASSASSINATION,
                    "assassination",
                    profile,
                    lambda: self._ai_player().assassinate(
                        state,
                        assassin.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                step_result = self._step_runner.apply_player_action(
                    state,
                    assassin.id,
                    "assassinate",
                    {"target_player_id": result.decision.target_player_id},
                )
                step_result = self._attach_ai_decision(
                    step_result,
                    game_id,
                    assassin.id,
                    Phase.ASSASSINATION,
                    "assassination",
                    profile,
                    result,
                )
                state = self._committer.commit_step(game_id, state, step_result)
            elif state.phase == Phase.LADY_OF_LAKE:
                holder_id = state.lady_of_lake_holder_player_id
                if holder_id is None:
                    self._set_state(game_id, state)
                    return state
                holder = next(player for player in state.players if player.id == holder_id)
                if holder.is_human:
                    self._set_state(game_id, state)
                    return state
                profile = self._profile_for_ai(game_id, holder.id)
                result = self._run_ai_turn(
                    game_id,
                    holder.id,
                    Phase.LADY_OF_LAKE,
                    "lady_of_lake",
                    profile,
                    lambda: self._ai_player().use_lady_of_lake(
                        state,
                        holder.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                step_result = self._step_runner.apply_player_action(
                    state,
                    holder.id,
                    "use_lady_of_lake",
                    {"target_player_id": result.decision.target_player_id},
                )
                step_result = self._attach_ai_decision(
                    step_result,
                    game_id,
                    holder.id,
                    Phase.LADY_OF_LAKE,
                    "lady_of_lake",
                    profile,
                    result,
                )
                state = self._committer.commit_step(game_id, state, step_result)
            else:
                self._set_state(game_id, state)
                return state
        raise RuntimeError("AI auto-advance exceeded safety limit")


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
    return state


def _human_player(state: GameState) -> Player:
    return next(player for player in state.players if player.is_human)
