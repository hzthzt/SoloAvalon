from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
from typing import Any, Callable

from backend.app.ai.player import AiDecisionError, AiPlayer, AiTurnResult
from backend.app.game.events import (
    build_game_created_payloads,
    build_private_view_payloads,
    build_roles_assigned_payloads,
)
from backend.app.game.models import (
    Faction,
    GameOption,
    GameState,
    LadyOfLakeInspection,
    MissionAction,
    Phase,
    Player,
    Role,
    Vote,
)
from backend.app.game.rules import (
    create_game as create_rules_game,
    eligible_lady_of_lake_target_ids,
)
from backend.app.llm.profiles import LlmProfile
from backend.app.storage.event_store import EventRecord, EventStore
from backend.app.storage.ai_decision_repository import (
    AiDecisionInput,
    AiDecisionRepository,
)
from backend.app.storage.ai_memory_repository import (
    AiMemoryRepository,
    AiMemorySnapshotInput,
)
from backend.app.storage.game_repository import GameRepository, GameSummary
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from .event_visibility import normalize_public_player_references, public_event_dicts
from .game_flow import (
    GameAdvanceLoop,
    GameCommitter,
    GameReplayError,
    GameStateLoader,
    GameStepRunner,
    PendingEvent,
    StepResult,
    apply_replay_event,
)


def _synchronized(method: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(self: "GameService", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class GameService:
    def __init__(self, connection: sqlite3.Connection, ai_player: AiPlayer | None = None):
        self._lock = threading.RLock()
        self._connection = connection
        self._games = GameRepository(connection)
        self._events = EventStore(connection)
        self._ai_decisions = AiDecisionRepository(connection)
        self._ai_memory = AiMemoryRepository(connection)
        self._profiles = LlmProfileRepository(connection)
        self._ai_player = ai_player or AiPlayer()
        self._states: dict[str, GameState] = {}
        self._step_runner = GameStepRunner()
        self._committer = GameCommitter(
            connection,
            self._games,
            self._events,
            self._ai_decisions,
            self._ai_memory,
            self._states,
        )
        self._advance_loop = GameAdvanceLoop(
            ai_player=lambda: self._ai_player,
            profile_for_ai=self._profile_for_ai,
            run_ai_turn=self._run_ai_turn,
            attach_ai_decision=self._step_with_ai_decision,
            public_events_for_ai=self._public_events_for_ai,
            next_human_action=self._next_human_action,
            set_state=self._set_state,
            step_runner=self._step_runner,
            committer=self._committer,
        )

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @_synchronized
    def create_game(
        self,
        *,
        seed: int | None = None,
        player_count: int = 5,
        enabled_options: set[GameOption | str] | frozenset[GameOption | str] | None = None,
        human_name: str | None = None,
        ai_names: list[str] | None = None,
        default_llm_profile_id: str | None = None,
        ai_profile_overrides: dict[str, str | None] | None = None,
        auto_advance: bool = True,
    ) -> dict[str, Any]:
        game_id = self._games.next_game_id()
        display_name = self._games.next_room_display_name()
        state = create_rules_game(
            player_count=player_count,
            seed=seed,
            human_name=human_name or "真人玩家",
            ai_names=ai_names or [],
            enabled_options=enabled_options,
        )
        state = _apply_ai_configuration(state, ai_profile_overrides or {})
        self._games.save_new_game(
            state,
            game_id=game_id,
            display_name=display_name,
            default_llm_profile_id=default_llm_profile_id,
        )
        self._states[game_id] = state
        self._append_event_pair(game_id, "game_created", build_game_created_payloads(state, game_id))
        self._append_event_pair(game_id, "roles_assigned", build_roles_assigned_payloads(state))
        for player in state.players:
            public_payload, private_payload = build_private_view_payloads(state, player.id)
            self._events.append_event(
                game_id,
                "private_view_recorded",
                public_payload=public_payload,
                private_payload=private_payload,
            )
        if auto_advance:
            state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    def get_game_state(self, game_id: str) -> dict[str, Any]:
        return self._public_state(game_id, self._state(game_id))

    @_synchronized
    def submit_human_action(
        self,
        game_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._ensure_playable(game_id)
        state = self._state(game_id)
        human = _human_player(state)
        expected = self._next_human_action(state)
        if action_type != expected:
            raise ValueError(f"expected human action {expected}, got {action_type}")

        step_result = self._step_runner.apply_human_action(
            game_id,
            state,
            action_type,
            payload,
            human_player_id=human.id,
        )
        state = self._committer.commit_step(game_id, state, step_result)
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    @_synchronized
    def submit_human_ai_action(self, game_id: str) -> dict[str, Any]:
        self._ensure_playable(game_id)
        state = self._state(game_id)
        human = _human_player(state)
        action_type = self._next_human_action(state)
        if action_type is None:
            raise ValueError("no human action is available for AI control")

        profile = self._profile_for_ai(game_id, human.id)
        if action_type == "propose_team":
            result = self._run_ai_turn(
                game_id,
                human.id,
                Phase.TEAM_PROPOSAL,
                "team_proposal",
                profile,
                lambda: self._ai_player.propose_team(
                    state,
                    human.id,
                    profile,
                    public_events=self._public_events_for_ai(game_id),
                ),
            )
            step_result = self._step_runner.apply_player_action(
                state,
                human.id,
                "propose_team",
                {"team": result.decision.team},
            )
            step_result = self._step_with_ai_decision(
                step_result,
                game_id,
                human.id,
                Phase.TEAM_PROPOSAL,
                "team_proposal",
                profile,
                result,
            )
        elif action_type == "speak":
            result = self._run_ai_turn(
                game_id,
                human.id,
                Phase.SPEECH,
                "speech",
                profile,
                lambda: self._ai_player.speak(
                    state,
                    human.id,
                    profile,
                    public_events=self._public_events_for_ai(game_id),
                ),
            )
            step_result = self._step_runner.apply_player_action(
                state,
                human.id,
                "speak",
                {"message": result.decision.public_message},
            )
            step_result = self._step_with_ai_decision(
                step_result,
                game_id,
                human.id,
                Phase.SPEECH,
                "speech",
                profile,
                result,
            )
        elif action_type == "vote":
            result = self._run_ai_turn(
                game_id,
                human.id,
                Phase.VOTING,
                "vote",
                profile,
                lambda: self._ai_player.vote(
                    state,
                    human.id,
                    profile,
                    public_events=self._public_events_for_ai(game_id),
                ),
            )
            step_result = self._step_runner.apply_player_action(
                state,
                human.id,
                "vote",
                {"vote": result.decision.vote.value},
            )
            step_result = self._step_with_ai_decision(
                step_result,
                game_id,
                human.id,
                Phase.VOTING,
                "vote",
                profile,
                result,
            )
        elif action_type == "mission_action":
            if human.faction == Faction.GOOD:
                step_result = self._step_runner.apply_player_action(
                    state,
                    human.id,
                    "mission_action",
                    {"mission_action": MissionAction.SUCCESS.value},
                )
            else:
                result = self._run_ai_turn(
                    game_id,
                    human.id,
                    Phase.QUEST,
                    "mission_action",
                    profile,
                    lambda: self._ai_player.mission_action(
                        state,
                        human.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                step_result = self._step_runner.apply_player_action(
                    state,
                    human.id,
                    "mission_action",
                    {"mission_action": result.decision.mission_action.value},
                )
                step_result = self._step_with_ai_decision(
                    step_result,
                    game_id,
                    human.id,
                    Phase.QUEST,
                    "mission_action",
                    profile,
                    result,
                )
        elif action_type == "assassinate":
            result = self._run_ai_turn(
                game_id,
                human.id,
                Phase.ASSASSINATION,
                "assassination",
                profile,
                lambda: self._ai_player.assassinate(
                    state,
                    human.id,
                    profile,
                    public_events=self._public_events_for_ai(game_id),
                ),
            )
            step_result = self._step_runner.apply_player_action(
                state,
                human.id,
                "assassinate",
                {"target_player_id": result.decision.target_player_id},
            )
            step_result = self._step_with_ai_decision(
                step_result,
                game_id,
                human.id,
                Phase.ASSASSINATION,
                "assassination",
                profile,
                result,
            )
        elif action_type == "use_lady_of_lake":
            result = self._run_ai_turn(
                game_id,
                human.id,
                Phase.LADY_OF_LAKE,
                "lady_of_lake",
                profile,
                lambda: self._ai_player.use_lady_of_lake(
                    state,
                    human.id,
                    profile,
                    public_events=self._public_events_for_ai(game_id),
                ),
            )
            step_result = self._step_runner.apply_player_action(
                state,
                human.id,
                "use_lady_of_lake",
                {"target_player_id": result.decision.target_player_id},
            )
            step_result = self._step_with_ai_decision(
                step_result,
                game_id,
                human.id,
                Phase.LADY_OF_LAKE,
                "lady_of_lake",
                profile,
                result,
            )
        else:
            raise ValueError(f"unknown human action type: {action_type}")

        state = self._committer.commit_step(game_id, state, step_result)
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    @_synchronized
    def retry_paused_game(self, game_id: str) -> dict[str, Any]:
        self._ensure_playable(game_id)
        state = self._reload_state(game_id)
        if state.phase == Phase.COMPLETE:
            return self._public_state(game_id, state)

        summary = self._games.get_game_summary(game_id)
        if summary is None:
            raise ValueError(f"unknown active game id: {game_id}")
        if summary.status != "error_paused":
            return self._public_state(game_id, state)

        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    @_synchronized
    def advance_game(self, game_id: str) -> dict[str, Any]:
        self._ensure_playable(game_id)
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    def list_games(self) -> list[GameSummary]:
        return self._games.list_games()

    @_synchronized
    def delete_game(self, game_id: str) -> None:
        self._states.pop(game_id, None)
        self._games.delete_game(game_id)

    @_synchronized
    def archive_game(self, game_id: str) -> GameSummary:
        return self._games.archive_game(game_id)

    def list_events(self, game_id: str) -> list[EventRecord]:
        return self._events.list_events(game_id)

    def list_events_after(self, game_id: str, event_index: int) -> list[EventRecord]:
        return self._events.list_events_after(game_id, event_index)

    def export_game_log(self, game_id: str, include_private: bool = False) -> dict[str, Any]:
        return self._events.export_game_log(game_id, include_private=include_private)

    def get_room_detail(self, game_id: str) -> dict[str, Any]:
        game = self.get_game_state(game_id)
        events = [asdict(event) for event in self._events.list_events(game_id)]
        decisions = self._ai_decisions.list_decisions(game_id)
        decision_rows = [asdict(decision) for decision in decisions]
        players = {player.id: player.name for player in self._games.list_players(game_id)}
        return {
            "game": game,
            "events": events,
            "ai_decisions": decision_rows,
            "usage_by_player": _usage_by_player(decision_rows, players),
            "usage_by_model": _usage_by_model(decision_rows),
        }

    def _auto_advance(self, game_id: str) -> GameState:
        state = self._state(game_id)
        return self._advance_loop.advance_until_blocked(game_id, state)

    def _state(self, game_id: str) -> GameState:
        if game_id not in self._states:
            self._states[game_id] = self._restore_state(game_id)
        return self._states[game_id]

    def _reload_state(self, game_id: str) -> GameState:
        state = self._restore_state(game_id)
        self._states[game_id] = state
        return state

    def _restore_state(self, game_id: str) -> GameState:
        loaded = GameStateLoader(self._games, self._events).load(game_id)
        if loaded.replay_error is not None:
            raise GameReplayError(
                game_id,
                last_replayed_event_index=loaded.last_replayed_event_index,
                failed_event_index=loaded.failed_event_index or 0,
                cause=loaded.replay_error,
            )
        return loaded.state

    def _set_state(self, game_id: str, state: GameState) -> None:
        self._states[game_id] = state
        self._games.update_game_state(game_id, state)

    def _ensure_playable(self, game_id: str) -> None:
        summary = self._games.get_game_summary(game_id)
        if summary is None:
            raise ValueError(f"unknown active game id: {game_id}")
        if summary.archived_at is not None:
            raise ValueError("archived game cannot be played")

    def _profile_for_ai(self, game_id: str, player_id: str) -> LlmProfile:
        try:
            return self._profiles.resolve_profile_for_player(game_id, player_id)
        except ValueError:
            return LlmProfile(
                id="unconfigured",
                name="Unconfigured",
                base_url="",
                api_key="",
                model="unconfigured",
                temperature=0.0,
                timeout=1.0,
                created_at="",
                updated_at="",
            )

    def _public_events_for_ai(self, game_id: str) -> list[dict[str, Any]]:
        return public_event_dicts(self._events.list_events(game_id))

    def _public_state(self, game_id: str, state: GameState) -> dict[str, Any]:
        summary = self._games.get_game_summary(game_id)
        human = _human_player(state)
        view_payload = build_private_view_payloads(state, human.id)[1]
        visible_roles = view_payload["visible_roles"]
        return {
            "id": game_id,
            "display_name": summary.display_name if summary else game_id,
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
            "next_human_action": self._next_human_action(state),
            "events": public_event_dicts(self._events.list_events(game_id)),
        }

    def _next_human_action(self, state: GameState) -> str | None:
        human = _human_player(state)
        if state.phase == Phase.TEAM_PROPOSAL and state.players[state.leader_index].id == human.id:
            return "propose_team"
        if state.phase == Phase.SPEECH:
            if len(state.speeches) < len(state.speech_order):
                next_speaker_id = state.speech_order[len(state.speeches)]
                if next_speaker_id == human.id:
                    return "speak"
        if state.phase == Phase.VOTING and human.id not in state.votes:
            return "vote"
        if (
            state.phase == Phase.QUEST
            and human.id in state.proposed_team
            and human.id not in state.quest_actions
        ):
            return "mission_action"
        if state.phase == Phase.ASSASSINATION and human.role.value == "assassin":
            return "assassinate"
        if state.phase == Phase.LADY_OF_LAKE and state.lady_of_lake_holder_player_id == human.id:
            return "use_lady_of_lake"
        return None

    def _append_event_pair(
        self,
        game_id: str,
        event_type: str,
        payloads: tuple[dict[str, Any], dict[str, Any] | None],
    ) -> None:
        public_payload, private_payload = payloads
        self._events.append_event(game_id, event_type, public_payload, private_payload)

    def _run_ai_turn(
        self,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        call: Callable[[], AiTurnResult[Any]],
    ) -> AiTurnResult[Any]:
        try:
            return call()
        except AiDecisionError as exc:
            ai_decision, audit_event = self._ai_error_audit(
                game_id,
                player_id,
                phase,
                decision_type,
                profile,
                exc,
            )
            self._committer.commit_ai_error(game_id, ai_decision, audit_event)
            raise

    def _ai_error_audit(
        self,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        error: AiDecisionError,
    ) -> tuple[AiDecisionInput, PendingEvent]:
        output = {
            "error_type": error.error_type,
            "error_message": error.error_message,
        }
        return (
            AiDecisionInput(
                game_id=game_id,
                player_id=player_id,
                phase=phase.value,
                decision_type=decision_type,
                input_summary=error.input_summary,
                strategy_summary=error.strategy_summary,
                output=output,
                model_name=profile.model,
                llm_profile_id=None if profile.id == "unconfigured" else profile.id,
                prompt_template_name=error.prompt_template_name,
                prompt_template_version=error.prompt_template_version,
                context_builder_version=error.context_builder_version,
                stable_prefix_hash=error.stable_prefix_hash,
                cache_strategy="stable-prefix-v1",
                context_summary=error.context_summary,
                context_truncated=error.context_truncated,
                output_raw=error.output_raw,
                output_parsed=error.output_parsed,
                validation_status=error.validation_status,
                prompt_tokens=error.prompt_tokens,
                completion_tokens=error.completion_tokens,
                total_tokens=error.total_tokens,
                cached_tokens=error.cached_tokens,
                cache_hit_rate=error.cache_hit_rate,
            ),
            PendingEvent(
                "ai_decision",
                {
                    "player_id": player_id,
                    "phase": phase.value,
                    "decision_type": decision_type,
                    "validation_status": error.validation_status,
                    "strategy_summary": error.strategy_summary,
                },
                {
                    "input_summary": error.input_summary,
                    "output_raw": error.output_raw,
                    "output_parsed": error.output_parsed,
                    "error_type": error.error_type,
                    "error_message": error.error_message,
                    "prompt_template_name": error.prompt_template_name,
                    "prompt_template_version": error.prompt_template_version,
                    "prompt_messages": error.prompt_messages,
                    "context_builder_version": error.context_builder_version,
                    "stable_prefix_hash": error.stable_prefix_hash,
                    "context_summary": error.context_summary,
                    "context_truncated": error.context_truncated,
                    "prompt_tokens": error.prompt_tokens,
                    "completion_tokens": error.completion_tokens,
                    "total_tokens": error.total_tokens,
                    "cached_tokens": error.cached_tokens,
                    "cache_hit_rate": error.cache_hit_rate,
                },
            ),
        )

    def _step_with_ai_decision(
        self,
        step_result: StepResult,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        result: AiTurnResult[Any],
    ) -> StepResult:
        return StepResult(
            state_after=step_result.state_after,
            rule_events=step_result.rule_events,
            audit_events=step_result.audit_events
            + (
                PendingEvent(
                    "ai_decision",
                    {
                        "player_id": player_id,
                        "phase": phase.value,
                        "decision_type": decision_type,
                        "validation_status": result.validation_status,
                        "strategy_summary": result.strategy_summary,
                    },
                    {
                        "input_summary": result.input_summary,
                        "output_raw": result.output_raw,
                        "output_parsed": result.output_parsed,
                        "prompt_template_name": result.prompt_template_name,
                        "prompt_template_version": result.prompt_template_version,
                        "prompt_messages": result.prompt_messages,
                        "context_builder_version": result.context_builder_version,
                        "stable_prefix_hash": result.stable_prefix_hash,
                        "context_summary": result.context_summary,
                        "context_truncated": result.context_truncated,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "total_tokens": result.total_tokens,
                        "cached_tokens": result.cached_tokens,
                        "cache_hit_rate": result.cache_hit_rate,
                    },
                ),
            ),
            ai_decision=AiDecisionInput(
                game_id=game_id,
                player_id=player_id,
                phase=phase.value,
                decision_type=decision_type,
                input_summary=result.input_summary,
                strategy_summary=result.strategy_summary,
                output=_json_ready(result.decision),
                model_name=profile.model,
                llm_profile_id=None if profile.id == "unconfigured" else profile.id,
                prompt_template_name=result.prompt_template_name,
                prompt_template_version=result.prompt_template_version,
                context_builder_version=result.context_builder_version,
                stable_prefix_hash=result.stable_prefix_hash,
                cache_strategy="stable-prefix-v1",
                context_summary=result.context_summary,
                context_truncated=result.context_truncated,
                output_raw=result.output_raw,
                output_parsed=result.output_parsed,
                validation_status=result.validation_status,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cached_tokens=result.cached_tokens,
                cache_hit_rate=result.cache_hit_rate,
            ),
            ai_memory=AiMemorySnapshotInput(
                game_id=game_id,
                player_id=player_id,
                round_number=self._state(game_id).current_round,
                phase=phase.value,
                memory_payload=_memory_payload_from_decision(player_id, decision_type, result),
            ),
        )


def _apply_replay_event(state: GameState, event: EventRecord) -> GameState:
    return apply_replay_event(state, event)


def _apply_ai_configuration(
    state: GameState,
    ai_profile_overrides: dict[str, str | None],
) -> GameState:
    override_values = list(ai_profile_overrides.values())
    ai_index = 0
    players: list[Player] = []
    for player in state.players:
        if player.is_human:
            players.append(player)
            continue
        profile_id = override_values[ai_index] if ai_index < len(override_values) else None
        players.append(
            replace(
                player,
                llm_profile_id=profile_id,
            )
        )
        ai_index += 1
    return replace(state, players=tuple(players))

def _human_player(state: GameState) -> Player:
    return next(player for player in state.players if player.is_human)


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


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


def _memory_payload_from_decision(
    player_id: str,
    decision_type: str,
    result: AiTurnResult[Any],
) -> dict[str, Any]:
    observation = (
        f"{player_id} {decision_type} decision used {result.validation_status}: "
        f"{result.strategy_summary}"
    ).strip()
    return {
        "suspicions": {},
        "trusted_players": [player_id],
        "key_observations": [observation],
        "merlin_candidates": [],
    }
