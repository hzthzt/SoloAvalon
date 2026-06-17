from __future__ import annotations

import sqlite3
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from backend.app.ai.player import AiDecisionError, AiPlayer, AiTurnResult
from backend.app.game.events import (
    build_game_created_payloads,
    build_private_view_payloads,
    build_roles_assigned_payloads,
)
from backend.app.game.models import Faction, GameState, MissionAction, Phase, Player, Role, Vote
from backend.app.game.rules import (
    FIVE_PLAYER_MISSIONS,
    assassinate,
    cast_vote,
    create_five_player_game,
    finalize_quest,
    finalize_vote,
    propose_team,
    record_speech,
    submit_quest_action,
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
from .event_visibility import public_event_dicts


class GameService:
    def __init__(self, connection: sqlite3.Connection, ai_player: AiPlayer | None = None):
        self._connection = connection
        self._games = GameRepository(connection)
        self._events = EventStore(connection)
        self._ai_decisions = AiDecisionRepository(connection)
        self._ai_memory = AiMemoryRepository(connection)
        self._profiles = LlmProfileRepository(connection)
        self._ai_player = ai_player or AiPlayer()
        self._states: dict[str, GameState] = {}

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def create_game(
        self,
        *,
        seed: int | None = None,
        ai_names: list[str] | None = None,
        default_llm_profile_id: str | None = None,
        ai_profile_overrides: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        game_id = _timestamp_game_id()
        state = create_five_player_game(seed=seed)
        state = _apply_ai_configuration(state, ai_names or [], ai_profile_overrides or {})
        self._games.save_new_game(
            state,
            game_id=game_id,
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
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    def get_game_state(self, game_id: str) -> dict[str, Any]:
        return self._public_state(game_id, self._state(game_id))

    def submit_human_action(
        self,
        game_id: str,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._state(game_id)
        human = _human_player(state)
        expected = self._next_human_action(state)
        if action_type != expected:
            raise ValueError(f"expected human action {expected}, got {action_type}")

        if action_type == "propose_team":
            state = propose_team(state, human.id, tuple(payload["team"]))
            self._events.append_event(
                game_id,
                "team_proposed",
                {"leader_player_id": human.id, "team": list(state.proposed_team)},
            )
        elif action_type == "speak":
            message = str(payload["message"])
            state = record_speech(state, human.id, message)
            self._events.append_event(
                game_id,
                "speech",
                {"player_id": human.id, "message": message},
            )
        elif action_type == "vote":
            vote = Vote(payload["vote"])
            state = cast_vote(state, human.id, vote)
            self._events.append_event(
                game_id,
                "vote_cast",
                {"player_id": human.id, "vote": vote.value},
            )
        elif action_type == "mission_action":
            action = MissionAction(payload["mission_action"])
            state = submit_quest_action(state, human.id, action)
            self._events.append_event(
                game_id,
                "quest_action_submitted",
                {"player_id": human.id},
                {"mission_action": action.value},
            )
        elif action_type == "assassinate":
            target = str(payload["target_player_id"])
            state = assassinate(state, human.id, target)
            self._events.append_event(
                game_id,
                "assassination",
                {
                    "assassin_player_id": human.id,
                    "target_player_id": target,
                    "winner": state.winner.value if state.winner else None,
                },
            )
        else:
            raise ValueError(f"unknown action type: {action_type}")

        self._set_state(game_id, state)
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    def submit_human_ai_action(self, game_id: str) -> dict[str, Any]:
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
            state = propose_team(state, human.id, result.decision.team)
            self._log_ai_decision(game_id, human.id, Phase.TEAM_PROPOSAL, "team_proposal", profile, result)
            self._events.append_event(
                game_id,
                "team_proposed",
                {"leader_player_id": human.id, "team": list(state.proposed_team)},
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
            state = record_speech(state, human.id, result.decision.public_message)
            self._log_ai_decision(game_id, human.id, Phase.SPEECH, "speech", profile, result)
            self._events.append_event(
                game_id,
                "speech",
                {"player_id": human.id, "message": result.decision.public_message},
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
            state = cast_vote(state, human.id, result.decision.vote)
            self._log_ai_decision(game_id, human.id, Phase.VOTING, "vote", profile, result)
            self._events.append_event(
                game_id,
                "vote_cast",
                {"player_id": human.id, "vote": result.decision.vote.value},
            )
        elif action_type == "mission_action":
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
            state = submit_quest_action(state, human.id, result.decision.mission_action)
            self._log_ai_decision(game_id, human.id, Phase.QUEST, "mission_action", profile, result)
            self._events.append_event(
                game_id,
                "quest_action_submitted",
                {"player_id": human.id},
                {"mission_action": result.decision.mission_action.value},
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
            state = assassinate(state, human.id, result.decision.target_player_id)
            self._log_ai_decision(game_id, human.id, Phase.ASSASSINATION, "assassination", profile, result)
            self._events.append_event(
                game_id,
                "assassination",
                {
                    "assassin_player_id": human.id,
                    "target_player_id": result.decision.target_player_id,
                    "winner": state.winner.value if state.winner else None,
                },
            )
        else:
            raise ValueError(f"unknown human action type: {action_type}")

        self._set_state(game_id, state)
        state = self._auto_advance(game_id)
        return self._public_state(game_id, state)

    def list_games(self) -> list[GameSummary]:
        return self._games.list_games()

    def delete_game(self, game_id: str) -> None:
        self._states.pop(game_id, None)
        self._games.delete_game(game_id)

    def list_events(self, game_id: str) -> list[EventRecord]:
        return self._events.list_events(game_id)

    def export_game_log(self, game_id: str, include_private: bool = False) -> dict[str, Any]:
        return self._events.export_game_log(game_id, include_private=include_private)

    def _auto_advance(self, game_id: str) -> GameState:
        state = self._state(game_id)
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
                    lambda: self._ai_player.propose_team(
                        state,
                        leader.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                state = propose_team(state, leader.id, result.decision.team)
                self._log_ai_decision(game_id, leader.id, state.phase, "team_proposal", profile, result)
                self._events.append_event(
                    game_id,
                    "team_proposed",
                    {"leader_player_id": leader.id, "team": list(state.proposed_team)},
                )
            elif state.phase == Phase.SPEECH:
                next_speaker_id = state.speech_order[len(state.speeches)]
                profile = self._profile_for_ai(game_id, next_speaker_id)
                result = self._run_ai_turn(
                    game_id,
                    next_speaker_id,
                    Phase.SPEECH,
                    "speech",
                    profile,
                    lambda: self._ai_player.speak(
                        state,
                        next_speaker_id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                state = record_speech(state, next_speaker_id, result.decision.public_message)
                self._log_ai_decision(game_id, next_speaker_id, Phase.SPEECH, "speech", profile, result)
                self._events.append_event(
                    game_id,
                    "speech",
                    {"player_id": next_speaker_id, "message": result.decision.public_message},
                )
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
                        lambda: self._ai_player.vote(
                            state,
                            player.id,
                            profile,
                            public_events=self._public_events_for_ai(game_id),
                        ),
                    )
                    state = cast_vote(state, player.id, result.decision.vote)
                    self._log_ai_decision(game_id, player.id, Phase.VOTING, "vote", profile, result)
                    self._events.append_event(
                        game_id,
                        "vote_cast",
                        {"player_id": player.id, "vote": result.decision.vote.value},
                    )
                if human.id not in state.votes:
                    self._set_state(game_id, state)
                    return state
                state = finalize_vote(state)
                self._events.append_event(
                    game_id,
                    "vote_result",
                    {
                        "approved": state.phase == Phase.QUEST,
                        "failed_team_votes": state.failed_team_votes,
                    },
                )
            elif state.phase == Phase.QUEST:
                human = _human_player(state)
                for player_id in state.proposed_team:
                    if player_id == human.id or player_id in state.quest_actions:
                        continue
                    profile = self._profile_for_ai(game_id, player_id)
                    result = self._run_ai_turn(
                        game_id,
                        player_id,
                        Phase.QUEST,
                        "mission_action",
                        profile,
                        lambda: self._ai_player.mission_action(
                            state,
                            player_id,
                            profile,
                            public_events=self._public_events_for_ai(game_id),
                        ),
                    )
                    state = submit_quest_action(state, player_id, result.decision.mission_action)
                    self._log_ai_decision(game_id, player_id, Phase.QUEST, "mission_action", profile, result)
                    self._events.append_event(
                        game_id,
                        "quest_action_submitted",
                        {"player_id": player_id},
                        {"mission_action": result.decision.mission_action.value},
                    )
                if human.id in state.proposed_team and human.id not in state.quest_actions:
                    self._set_state(game_id, state)
                    return state
                state = finalize_quest(state)
                self._events.append_event(
                    game_id,
                    "quest_result",
                    {
                        "quest_results": ["success" if result else "fail" for result in state.quest_results],
                        "phase": state.phase.value,
                        "winner": state.winner.value if state.winner else None,
                    },
                )
            elif state.phase == Phase.ASSASSINATION:
                assassin = next(player for player in state.players if player.role.value == "assassin")
                profile = self._profile_for_ai(game_id, assassin.id)
                result = self._run_ai_turn(
                    game_id,
                    assassin.id,
                    Phase.ASSASSINATION,
                    "assassination",
                    profile,
                    lambda: self._ai_player.assassinate(
                        state,
                        assassin.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                state = assassinate(state, assassin.id, result.decision.target_player_id)
                self._log_ai_decision(game_id, assassin.id, Phase.ASSASSINATION, "assassination", profile, result)
                self._events.append_event(
                    game_id,
                    "assassination",
                    {
                        "assassin_player_id": assassin.id,
                        "target_player_id": result.decision.target_player_id,
                        "winner": state.winner.value if state.winner else None,
                    },
                )
            else:
                self._set_state(game_id, state)
                return state
        raise RuntimeError("AI auto-advance exceeded safety limit")

    def _state(self, game_id: str) -> GameState:
        if game_id not in self._states:
            self._states[game_id] = self._restore_state(game_id)
        return self._states[game_id]

    def _restore_state(self, game_id: str) -> GameState:
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
                llm_profile_id=player.llm_profile_id,
            )
            for player in self._games.list_players(game_id)
        )
        if not players:
            raise ValueError(f"game has no persisted players: {game_id}")

        state = GameState(players=players, missions=FIVE_PLAYER_MISSIONS)
        for event in self._events.list_events(game_id):
            state = _apply_replay_event(state, event)
        return state

    def _set_state(self, game_id: str, state: GameState) -> None:
        self._states[game_id] = state
        self._games.update_game_state(game_id, state)

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
        human = _human_player(state)
        view_payload = build_private_view_payloads(state, human.id)[1]
        visible_roles = view_payload["visible_roles"]
        return {
            "id": game_id,
            "status": "complete" if state.winner else "active",
            "phase": state.phase.value,
            "current_round": state.current_round,
            "leader_player_id": state.players[state.leader_index].id,
            "human_player_id": human.id,
            "human_role": human.role.value,
            "human_faction": human.faction.value,
            "known_evil_player_ids": view_payload["known_evil_player_ids"],
            "players": [
                {
                    "id": player.id,
                    "seat_index": player.seat_index,
                    "name": player.name,
                    "is_human": player.is_human,
                    "visible_role": visible_roles[player.id],
                }
                for player in state.players
            ],
            "proposed_team": list(state.proposed_team),
            "speech_order": list(state.speech_order),
            "speeches": dict(state.speeches),
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
            self._log_ai_error(game_id, player_id, phase, decision_type, profile, exc)
            raise

    def _log_ai_error(
        self,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        error: AiDecisionError,
    ) -> None:
        output = {
            "error_type": error.error_type,
            "error_message": error.error_message,
        }
        self._ai_decisions.save_decision(
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
            )
        )
        self._events.append_event(
            game_id,
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
            },
        )

    def _log_ai_decision(
        self,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        result: AiTurnResult[Any],
    ) -> None:
        self._ai_decisions.save_decision(
            AiDecisionInput(
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
            )
        )
        self._ai_memory.save_snapshot(
            AiMemorySnapshotInput(
                game_id=game_id,
                player_id=player_id,
                round_number=self._state(game_id).current_round,
                phase=phase.value,
                memory_payload=_memory_payload_from_decision(player_id, decision_type, result),
            )
        )
        self._events.append_event(
            game_id,
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
            },
        )


def _apply_replay_event(state: GameState, event: EventRecord) -> GameState:
    public_payload = event.public_payload
    private_payload = event.private_payload or {}
    if event.event_type in {
        "game_created",
        "roles_assigned",
        "private_view_recorded",
        "ai_decision",
    }:
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
    if event.event_type == "assassination":
        return assassinate(
            state,
            str(public_payload["assassin_player_id"]),
            str(public_payload["target_player_id"]),
        )
    return state


def _apply_ai_configuration(
    state: GameState,
    ai_names: list[str],
    ai_profile_overrides: dict[str, str | None],
) -> GameState:
    ai_index = 0
    players: list[Player] = []
    for player in state.players:
        if player.is_human:
            players.append(player)
            continue
        name = ai_names[ai_index] if ai_index < len(ai_names) and ai_names[ai_index] else player.name
        players.append(
            replace(
                player,
                name=name,
                llm_profile_id=ai_profile_overrides.get(player.id),
            )
        )
        ai_index += 1
    return replace(state, players=tuple(players))


def _timestamp_game_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


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
