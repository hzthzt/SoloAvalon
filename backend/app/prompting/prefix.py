from __future__ import annotations

from .config import PromptTemplateConfig


COMMON_ACTIONS = ("propose_team", "speak", "vote", "mission_action")


def common_action_prefix(config: PromptTemplateConfig) -> str:
    lines = [
        "【动作规则】",
        "只应用本次请求对应的动作规则；其他动作规则在轮到该动作时才生效。",
    ]
    for action in COMMON_ACTIONS:
        lines.extend(["", f"{action}:", *config.action_prompts[action]["lines"]])
    lines.extend(
        [
            "",
            "【动作 JSON 格式声明】",
            "每次只返回本次请求对应的 JSON，不附加其他动作或解释。",
        ]
    )
    for action in COMMON_ACTIONS:
        contract = config.action_prompts[action]["json"].replace(
            "{mission_actions}", "success|fail"
        )
        lines.extend([f"{action}:", contract])
    return "\n".join(lines)
