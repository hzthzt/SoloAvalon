from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from backend.app.ai.player import AiDecisionError, AiPlayer, AiTurnResult
from backend.app.errors import describe_error
from backend.app.game.models import GameState, Phase
from backend.app.llm.profiles import LlmProfile
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from backend.app.storage.ai_decision_repository import AiDecisionInput
from backend.app.storage.ai_memory_repository import AiMemorySnapshotInput
from .game_flow import PendingEvent, StepResult, GameStepRunner


class AiDecisionRunner:
    def __init__(self, player: AiPlayer, profiles: LlmProfileRepository):
        self.player = player
        self.profiles = profiles

    def profile_for(self, state: GameState, summary: Any, player_id: str) -> LlmProfile:
        player = next(player for player in state.players if player.id == player_id)
        profile_id = player.llm_profile_id or summary.default_llm_profile_id
        if profile_id:
            profile = self.profiles.get_profile(profile_id)
            if profile is not None:
                return profile
        return LlmProfile(id="unconfigured", name="Unconfigured", base_url="", api_key="",
                          model="unconfigured", reasoning_effort="none", timeout=1,
                          created_at="", updated_at="")

    def decide(self, game_id: str, state: GameState, player_id: str, action: str,
               profile: LlmProfile, events: list[dict[str, Any]]) -> StepResult:
        method, decision_type, field, attribute = {
            "propose_team": ("propose_team", "team_proposal", "team", "team"),
            "speak": ("speak", "speech", "message", "public_message"),
            "vote": ("vote", "vote", "vote", "vote"),
            "mission_action": ("mission_action", "mission_action", "mission_action", "mission_action"),
            "assassinate": ("assassinate", "assassination", "target_player_id", "target_player_id"),
            "use_lady_of_lake": ("use_lady_of_lake", "lady_of_lake", "target_player_id", "target_player_id"),
        }[action]
        result = getattr(self.player, method)(state, player_id, profile, public_events=events)
        value = _json_ready(getattr(result.decision, attribute))
        step = GameStepRunner().apply_player_action(state, player_id, action, {field: value})
        return self._step_with_ai_decision(step, game_id, player_id, state.phase,
                                           decision_type, profile, result, state.current_round)

    def error_audit(self, game_id, state, player_id, action, profile, error):
        decision_type = {"propose_team": "team_proposal", "speak": "speech",
                         "assassinate": "assassination", "use_lady_of_lake": "lady_of_lake"}.get(action, action)
        return self._ai_error_audit(game_id, player_id, state.phase, decision_type, profile, error)

    def _ai_error_audit(
        self,
        game_id: str,
        player_id: str,
        phase: Phase,
        decision_type: str,
        profile: LlmProfile,
        error: AiDecisionError,
    ) -> tuple[AiDecisionInput, PendingEvent]:
        detail = describe_error(error, ai=True)
        output = {**detail, "error_message": detail["message"]}
        strategy_summary = f"AI 决策失败：{detail['message']}"
        return (
            AiDecisionInput(
                game_id=game_id,
                player_id=player_id,
                phase=phase.value,
                decision_type=decision_type,
                input_summary=error.input_summary,
                strategy_summary=strategy_summary,
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
                    "strategy_summary": strategy_summary,
                },
                {
                    "input_summary": error.input_summary,
                    "output_raw": error.output_raw,
                    "output_parsed": error.output_parsed,
                    **output,
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
        round_number: int,
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
                round_number=round_number,
                phase=phase.value,
                memory_payload=_memory_payload_from_decision(player_id, decision_type, result),
            ),
        )


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
