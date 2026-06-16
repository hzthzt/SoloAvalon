from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from backend.app.game.models import GameState, Phase
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.schemas import (
    assassination_decision_from_output,
    mission_decision_from_output,
    parse_json_object,
    speech_decision_from_output,
    team_decision_from_output,
    vote_decision_from_output,
)
from backend.app.prompting.templates import PROMPT_TEMPLATE_VERSION, PromptBuilder

from .context import ContextBuilder
from .strategy import (
    AssassinationDecision,
    FallbackStrategy,
    MissionActionDecision,
    SpeechDecision,
    TeamProposalDecision,
    VoteDecision,
)


DecisionT = TypeVar("DecisionT")


@dataclass(frozen=True)
class AiTurnResult(Generic[DecisionT]):
    decision: DecisionT
    input_summary: str
    strategy_summary: str
    output_raw: str | None
    output_parsed: dict[str, Any] | None
    validation_status: str
    prompt_template_name: str
    prompt_template_version: str
    context_builder_version: str
    stable_prefix_hash: str
    context_summary: str
    context_truncated: bool


class AiPlayer:
    def __init__(
        self,
        provider: LlmProvider | Any | None = None,
        context_builder: ContextBuilder | None = None,
        prompt_builder: PromptBuilder | None = None,
        fallback_strategy: FallbackStrategy | None = None,
    ):
        self._provider = provider or LlmProvider()
        self._context_builder = context_builder or ContextBuilder()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._fallback = fallback_strategy or FallbackStrategy()

    def propose_team(
        self,
        state: GameState,
        leader_player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[TeamProposalDecision]:
        return self._decide(
            state,
            leader_player_id,
            profile,
            Phase.TEAM_PROPOSAL,
            lambda output: team_decision_from_output(output, state),
            lambda: self._fallback.propose_team(state, leader_player_id),
            public_events=public_events,
        )

    def speak(
        self,
        state: GameState,
        player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[SpeechDecision]:
        return self._decide(
            state,
            player_id,
            profile,
            Phase.SPEECH,
            speech_decision_from_output,
            lambda: self._fallback.speak(state, player_id),
            public_events=public_events,
        )

    def vote(
        self,
        state: GameState,
        player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[VoteDecision]:
        return self._decide(
            state,
            player_id,
            profile,
            Phase.VOTING,
            vote_decision_from_output,
            lambda: self._fallback.vote(state, player_id),
            public_events=public_events,
        )

    def mission_action(
        self,
        state: GameState,
        player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[MissionActionDecision]:
        return self._decide(
            state,
            player_id,
            profile,
            Phase.QUEST,
            lambda output: mission_decision_from_output(output, state, player_id),
            lambda: self._fallback.mission_action(state, player_id),
            public_events=public_events,
        )

    def assassinate(
        self,
        state: GameState,
        assassin_player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[AssassinationDecision]:
        return self._decide(
            state,
            assassin_player_id,
            profile,
            Phase.ASSASSINATION,
            lambda output: assassination_decision_from_output(output, state, assassin_player_id),
            lambda: self._fallback.assassinate(state, assassin_player_id),
            public_events=public_events,
        )

    def _decide(
        self,
        state: GameState,
        player_id: str,
        profile: LlmProfile,
        phase: Phase,
        parse_decision: Callable[[dict[str, Any]], DecisionT],
        fallback: Callable[[], DecisionT],
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[DecisionT]:
        context = self._context_builder.build(state, player_id, phase, public_events=public_events)
        messages = self._prompt_builder.build_messages(context, phase)
        output_raw: str | None = None
        output_parsed: dict[str, Any] | None = None
        try:
            output_raw = self._provider.chat_completion(profile, messages)
            output_parsed = parse_json_object(output_raw)
            decision = parse_decision(output_parsed)
            validation_status = "valid"
        except Exception:
            decision = fallback()
            validation_status = "fallback"
        return AiTurnResult(
            decision=decision,
            input_summary=context.context_summary,
            strategy_summary=getattr(decision, "private_reason_summary", ""),
            output_raw=output_raw,
            output_parsed=output_parsed,
            validation_status=validation_status,
            prompt_template_name=phase.value,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            context_builder_version=context.context_builder_version,
            stable_prefix_hash=context.stable_prefix_hash,
            context_summary=context.context_summary,
            context_truncated=context.context_truncated,
        )
