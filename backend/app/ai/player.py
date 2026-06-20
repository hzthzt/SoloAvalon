from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from backend.app.game.models import GameState, Phase
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.schemas import (
    assassination_decision_from_output,
    lady_of_lake_decision_from_output,
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
    LadyOfLakeDecision,
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
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_hit_rate: float | None = None
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
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_tokens: int | None = None,
        cache_hit_rate: float | None = None,
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
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.cached_tokens = cached_tokens
        self.cache_hit_rate = cache_hit_rate


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

    def use_lady_of_lake(
        self,
        state: GameState,
        viewer_player_id: str,
        profile: LlmProfile,
        public_events: list[dict[str, Any]] | None = None,
    ) -> AiTurnResult[LadyOfLakeDecision]:
        return self._decide(
            state,
            viewer_player_id,
            profile,
            Phase.LADY_OF_LAKE,
            lambda output: lady_of_lake_decision_from_output(output, state, viewer_player_id),
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
        last_output_raw: str | None = None
        last_output_parsed: dict[str, Any] | None = None
        last_usage: dict[str, int | float | None] = _empty_usage()

        def raise_decision_error(
            exc: Exception,
            output_raw: str | None,
            output_parsed: dict[str, Any] | None,
            usage: dict[str, int | float | None],
        ) -> None:
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
                prompt_tokens=_usage_int(usage["prompt_tokens"]),
                completion_tokens=_usage_int(usage["completion_tokens"]),
                total_tokens=_usage_int(usage["total_tokens"]),
                cached_tokens=_usage_int(usage["cached_tokens"]),
                cache_hit_rate=_usage_float(usage["cache_hit_rate"]),
            ) from exc

        for retry_index in range(profile.timeout_retries + 1):
            output_raw: str | None = None
            output_parsed: dict[str, Any] | None = None
            usage = _empty_usage()
            try:
                completion = self._provider.chat_completion(profile, messages)
                output_raw = _completion_content(completion)
                usage = _completion_usage(completion)
            except Exception as exc:
                raise_decision_error(exc, output_raw, output_parsed, usage)

            try:
                decision, output_parsed = _parse_decision_output(
                    output_raw,
                    parse_decision,
                    parse_text_decision,
                )
            except Exception as exc:
                last_output_raw = output_raw
                last_output_parsed = output_parsed
                last_usage = usage
                if retry_index < profile.timeout_retries:
                    continue
                raise_decision_error(exc, last_output_raw, last_output_parsed, last_usage)
            else:
                validation_status = "valid"
                break
        else:
            raise RuntimeError("unreachable ai decision retry state")

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
            prompt_tokens=_usage_int(usage["prompt_tokens"]),
            completion_tokens=_usage_int(usage["completion_tokens"]),
            total_tokens=_usage_int(usage["total_tokens"]),
            cached_tokens=_usage_int(usage["cached_tokens"]),
            cache_hit_rate=_usage_float(usage["cache_hit_rate"]),
            prompt_messages=messages,
        )


def _parse_decision_output(
    output_raw: str,
    parse_decision: Callable[[dict[str, Any]], DecisionT],
    parse_text_decision: Callable[[str], DecisionT] | None,
) -> tuple[DecisionT, dict[str, Any] | None]:
    if parse_text_decision is None:
        output_parsed = parse_json_object(output_raw)
        return parse_decision(output_parsed), output_parsed

    try:
        output_parsed = parse_json_object(output_raw)
    except Exception as json_exc:
        try:
            return parse_text_decision(output_raw), None
        except Exception as text_exc:
            raise text_exc from json_exc
    return parse_decision(output_parsed), output_parsed


def _completion_content(completion: Any) -> str:
    content = getattr(completion, "content", completion)
    if not isinstance(content, str):
        raise TypeError("llm completion content must be a string")
    return content


def _completion_usage(completion: Any) -> dict[str, int | float | None]:
    return {
        "prompt_tokens": getattr(completion, "prompt_tokens", None),
        "completion_tokens": getattr(completion, "completion_tokens", None),
        "total_tokens": getattr(completion, "total_tokens", None),
        "cached_tokens": getattr(completion, "cached_tokens", None),
        "cache_hit_rate": getattr(completion, "cache_hit_rate", None),
    }


def _empty_usage() -> dict[str, int | float | None]:
    return {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
        "cache_hit_rate": None,
    }


def _usage_int(value: int | float | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_float(value: int | float | None) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
