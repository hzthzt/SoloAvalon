from __future__ import annotations

from backend.app.ai.context import AiContext
from backend.app.game.models import Phase


PROMPT_TEMPLATE_VERSION = "prompt.v1"


class PromptBuilder:
    def build_messages(self, context: AiContext, phase: Phase) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    f"{context.stable_prefix}\n\n"
                    f"Prompt template: {PROMPT_TEMPLATE_VERSION}\n"
                    f"Current phase contract:\n{_phase_contract(phase)}"
                ),
            },
            {
                "role": "user",
                "content": (
                    "Use this private, filtered context for the current decision. "
                    "Return JSON only.\n"
                    f"{context.dynamic_private_suffix}"
                ),
            },
        ]


def _phase_contract(phase: Phase) -> str:
    if phase == Phase.TEAM_PROPOSAL:
        return '{"team":["player_id"],"private_reason_summary":"text","public_message":"text"}'
    if phase == Phase.SPEECH:
        return '{"public_message":"text","stance":"support_team|oppose_team|uncertain","private_reason_summary":"text"}'
    if phase == Phase.VOTING:
        return '{"vote":"approve|reject","private_reason_summary":"text","public_reason":"text"}'
    if phase == Phase.QUEST:
        return '{"mission_action":"success|fail","private_reason_summary":"text"}'
    if phase == Phase.ASSASSINATION:
        return '{"target_player_id":"player_id","private_reason_summary":"text","candidate_ranking":["player_id"]}'
    return "{}"
