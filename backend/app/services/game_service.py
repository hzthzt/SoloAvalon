from __future__ import annotations

import sqlite3
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
    STANDARD_MISSION_CONFIGS,
    assassinate,
    cast_vote,
    create_game as create_rules_game,
    eligible_lady_of_lake_target_ids,
    finalize_quest,
    finalize_vote,
    propose_team,
    record_speech,
    submit_quest_action,
    use_lady_of_lake,
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
        player_count: int = 5,
        enabled_options: set[GameOption | str] | frozenset[GameOption | str] | None = None,
        human_name: str | None = None,
        ai_names: list[str] | None = None,
        default_llm_profile_id: str | None = None,
        ai_profile_overrides: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        game_id = self._games.next_room_game_id()
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
        elif action_type == "use_lady_of_lake":
            target = str(payload["target_player_id"])
            state = use_lady_of_lake(state, human.id, target)
            inspection = state.lady_of_lake_inspections[-1]
            self._events.append_event(
                game_id,
                "lady_of_lake_used",
                {
                    "viewer_player_id": human.id,
                    "target_player_id": target,
                    "next_holder_player_id": target,
                    "round_number": inspection.round_number,
                },
                {"target_faction": inspection.target_faction.value},
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
            if human.faction == Faction.GOOD:
                state = submit_quest_action(state, human.id, MissionAction.SUCCESS)
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
                state = submit_quest_action(state, human.id, result.decision.mission_action)
                self._log_ai_decision(game_id, human.id, Phase.QUEST, "mission_action", profile, result)
            self._events.append_event(
                game_id,
                "quest_action_submitted",
                {"player_id": human.id},
                {"mission_action": state.quest_actions[human.id].value},
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
            state = use_lady_of_lake(state, human.id, result.decision.target_player_id)
            self._log_ai_decision(game_id, human.id, Phase.LADY_OF_LAKE, "lady_of_lake", profile, result)
            inspection = state.lady_of_lake_inspections[-1]
            self._events.append_event(
                game_id,
                "lady_of_lake_used",
                {
                    "viewer_player_id": human.id,
                    "target_player_id": result.decision.target_player_id,
                    "next_holder_player_id": result.decision.target_player_id,
                    "round_number": inspection.round_number,
                },
                {"target_faction": inspection.target_faction.value},
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
                    player = next(player for player in state.players if player.id == player_id)
                    if player.faction == Faction.GOOD:
                        state = submit_quest_action(state, player_id, MissionAction.SUCCESS)
                        self._events.append_event(
                            game_id,
                            "quest_action_submitted",
                            {"player_id": player_id},
                            {"mission_action": MissionAction.SUCCESS.value},
                        )
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
                success_cards = sum(
                    1 for action in state.quest_actions.values() if action == MissionAction.SUCCESS
                )
                fail_cards = sum(
                    1 for action in state.quest_actions.values() if action == MissionAction.FAIL
                )
                state = finalize_quest(state)
                self._events.append_event(
                    game_id,
                    "quest_result",
                    {
                        "quest_results": ["success" if result else "fail" for result in state.quest_results],
                        "success_cards": success_cards,
                        "fail_cards": fail_cards,
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
                    lambda: self._ai_player.use_lady_of_lake(
                        state,
                        holder.id,
                        profile,
                        public_events=self._public_events_for_ai(game_id),
                    ),
                )
                state = use_lady_of_lake(state, holder.id, result.decision.target_player_id)
                self._log_ai_decision(game_id, holder.id, Phase.LADY_OF_LAKE, "lady_of_lake", profile, result)
                inspection = state.lady_of_lake_inspections[-1]
                self._events.append_event(
                    game_id,
                    "lady_of_lake_used",
                    {
                        "viewer_player_id": holder.id,
                        "target_player_id": result.decision.target_player_id,
                        "next_holder_player_id": result.decision.target_player_id,
                        "round_number": inspection.round_number,
                    },
                    {"target_faction": inspection.target_faction.value},
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
            self._log_ai_error(game_id, player_id, phase, decision_type, profile, exc)
            self._games.update_game_status(game_id, "error_paused")
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
                prompt_tokens=error.prompt_tokens,
                completion_tokens=error.completion_tokens,
                total_tokens=error.total_tokens,
                cached_tokens=error.cached_tokens,
                cache_hit_rate=error.cache_hit_rate,
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
                "prompt_tokens": error.prompt_tokens,
                "completion_tokens": error.completion_tokens,
                "total_tokens": error.total_tokens,
                "cached_tokens": error.cached_tokens,
                "cache_hit_rate": error.cache_hit_rate,
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
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cached_tokens=result.cached_tokens,
                cache_hit_rate=result.cache_hit_rate,
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
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "cached_tokens": result.cached_tokens,
                "cache_hit_rate": result.cache_hit_rate,
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
