from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from backend.app.game.models import GameState, Phase
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.schemas import (
    assassination_decision_from_output,
    mission_decision_from_output,
    parse_json_object,
    speech_decision_from_output,
    speech_decision_from_text,
    team_decision_from_output,
    vote_decision_from_output,
    vote_decision_from_text,
)
from backend.app.prompting.templates import PromptBuilder

from .context import ContextBuilder
from .strategy import (
    AssassinationDecision,
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
    prompt_messages: list[dict[str, str]] = field(default_factory=list)


class AiDecisionError(RuntimeError):
    def __init__(
        self,
        *,
        error_type: str,
        error_message: str,
        input_summary: str,
        output_raw: str | None,
        output_parsed: dict[str, Any] | None,
        prompt_template_name: str,
        prompt_template_version: str,
        context_builder_version: str,
        stable_prefix_hash: str,
        context_summary: str,
        context_truncated: bool,
        prompt_messages: list[dict[str, str]],
    ):
        super().__init__(error_message)
        self.error_type = error_type
        self.error_message = error_message
        self.input_summary = input_summary
        self.strategy_summary = f"AI 决策失败：{error_message}"
        self.output_raw = output_raw
        self.output_parsed = output_parsed
        self.validation_status = "error"
        self.prompt_template_name = prompt_template_name
        self.prompt_template_version = prompt_template_version
        self.context_builder_version = context_builder_version
        self.stable_prefix_hash = stable_prefix_hash
        self.context_summary = context_summary
        self.context_truncated = context_truncated
        self.prompt_messages = prompt_messages


class AiPlayer:
    def __init__(
        self,
        provider: LlmProvider | Any | None = None,
        context_builder: ContextBuilder | None = None,
        prompt_builder: PromptBuilder | None = None,
    ):
        self._provider = provider or LlmProvider()
        self._context_builder = context_builder or ContextBuilder()
        self._prompt_builder = prompt_builder or PromptBuilder()

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
            None,
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
            speech_decision_from_text,
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
            vote_decision_from_text,
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
            None,
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
            None,
            public_events=public_events,
        )

    def _decide(
        self,
        state: GameState,
        player_id: str,
        profile: LlmProfile,
        phase: Phase,
        parse_decision: Callable[[dict[str, Any]], DecisionT],
        parse_text_decision: Callable[[str], DecisionT] | None,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[DecisionT]:
        context = self._context_builder.build(state, player_id, phase, public_events=public_events)
        messages = self._prompt_builder.build_messages(context, phase)
        output_raw: str | None = None
        output_parsed: dict[str, Any] | None = None
        try:
            output_raw = self._provider.chat_completion(profile, messages)
            if parse_text_decision is None:
                output_parsed = parse_json_object(output_raw)
                decision = parse_decision(output_parsed)
            else:
                try:
                    output_parsed = parse_json_object(output_raw)
                except Exception:
                    output_parsed = None
                    decision = parse_text_decision(output_raw)
                else:
                    decision = parse_decision(output_parsed)
            validation_status = "valid"
        except Exception as exc:
            error_message = str(exc) or type(exc).__name__
            raise AiDecisionError(
                error_type=type(exc).__name__,
                error_message=error_message,
                input_summary=context.context_summary,
                output_raw=output_raw,
                output_parsed=output_parsed,
                prompt_template_name=phase.value,
                prompt_template_version=self._prompt_builder.version,
                context_builder_version=context.context_builder_version,
                stable_prefix_hash=context.stable_prefix_hash,
                context_summary=context.context_summary,
                context_truncated=context.context_truncated,
                prompt_messages=messages,
            ) from exc
        return AiTurnResult(
            decision=decision,
            input_summary=context.context_summary,
            strategy_summary=getattr(decision, "private_reason_summary", ""),
            output_raw=output_raw,
            output_parsed=output_parsed,
            validation_status=validation_status,
            prompt_template_name=phase.value,
            prompt_template_version=self._prompt_builder.version,
            context_builder_version=context.context_builder_version,
            stable_prefix_hash=context.stable_prefix_hash,
            context_summary=context.context_summary,
            context_truncated=context.context_truncated,
            prompt_messages=messages,
        )
