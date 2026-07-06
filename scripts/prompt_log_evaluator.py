from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PLAYER_REF = r"(?:玩家)?\d+号?"

IDENTITY_ACTION_PATTERNS = (
    rf"替{PLAYER_REF}卸压",
    rf"急着[排保]{PLAYER_REF}",
    rf"偏保{PLAYER_REF}",
    r"\d+号这轮先别被打死",
    rf"{PLAYER_REF}一直保{PLAYER_REF}",
    rf"(让|希望让|想让){PLAYER_REF}上车(试试|看看|吃压力)?",
    rf"{PLAYER_REF}.*急着上车",
    rf"{PLAYER_REF}.*坚持换人",
    rf"{PLAYER_REF}.*往车上推",
    rf"带上{PLAYER_REF}.*吃压力",
    rf"{PLAYER_REF}.*被排在外.*带上",
    rf"给{PLAYER_REF}上车机会",
    rf"{PLAYER_REF}.*给你机会上车",
    rf"{PLAYER_REF}.*(唯一反对|反对.*失败车).*给他上车机会",
    rf"{PLAYER_REF}.*反对.*给他一次上车机会",
    rf"{PLAYER_REF}.*上车吃压力看看态度",
    rf"{PLAYER_REF}.*上轮为什么反对车队",
    rf"问{PLAYER_REF}为什么反对后又失败",
    rf"问{PLAYER_REF}为什么没能维持成功",
    rf"{PLAYER_REF}.*被排除在车外.*(认可|换人|组队)",
    rf"{PLAYER_REF}.*给他上车机会看看表现",
    rf"{PLAYER_REF}.*给他机会看看表现",
    rf"{PLAYER_REF}.*解释一下当时为什么反对",
    rf"{PLAYER_REF}.*这次没带你.*该上.*理由",
    rf"{PLAYER_REF}.*任务交给你负责",
    rf"{PLAYER_REF}.*把{PLAYER_REF}.*(留在外面|排在外面|排除在外)",
    rf"{PLAYER_REF}.*排除了{PLAYER_REF}",
    rf"{PLAYER_REF}.*没带{PLAYER_REF}",
    rf"为什么没带{PLAYER_REF}",
    rf"为什么把{PLAYER_REF}(留在外面|排在外面|排除在外)",
    rf"为什么没把{PLAYER_REF}放进",
    rf"{PLAYER_REF}.*跳过{PLAYER_REF}",
    rf"{PLAYER_REF}.*解释压力",
    rf"{PLAYER_REF}.*被排在外.*带.*上车",
    rf"(带|组).{{0,32}}{PLAYER_REF}.{{0,50}}(失败|炸).{{0,16}}{PLAYER_REF}.{{0,10}}解释",
    rf"{PLAYER_REF}.{{0,24}}(失败|炸).{{0,16}}{PLAYER_REF}.{{0,10}}解释",
    rf"问{PLAYER_REF}为什么接受这个组合",
    rf"{PLAYER_REF}.*对{PLAYER_REF}.*信任程度",
    r"保这条.*线",
    r"拆这条.*线",
    r"关系不自然",
    r"不像普通好人互相评价",
    r"先站.*线",
    r"先拆.*线",
    r"挡一下",
)

CANDIDATE_RELATION_PRESSURE_PATTERNS = (
    rf"{PLAYER_REF}.*(一直|持续|急着).*(推|保|排|拆|换|卸压).*{PLAYER_REF}",
    rf"{PLAYER_REF}.*(一直|持续|急着).*{PLAYER_REF}.*(推|保|排|拆|换|卸压)",
    rf"{PLAYER_REF}.*(替|帮).+{PLAYER_REF}.*(说话|卸压|挡|保|争)",
    rf"{PLAYER_REF}.*为什么.*{PLAYER_REF}.*(更该上|上车|下车|被排|被保)",
    rf"{PLAYER_REF}.*为什么.*把{PLAYER_REF}.*(留在外面|排在外面|排除在外)",
    rf"{PLAYER_REF}.*为什么.*跳过{PLAYER_REF}",
    rf"{PLAYER_REF}.*跳过{PLAYER_REF}.*(理由|解释|为什么)",
    rf"{PLAYER_REF}.*把{PLAYER_REF}.*(留在外面|排在外面|排除在外).*(支持|反对|态度|解释|理由)",
    rf"带上{PLAYER_REF}.*吃压力",
    rf"{PLAYER_REF}.*被排在外.*带上",
    rf"给{PLAYER_REF}上车机会",
    rf"{PLAYER_REF}.*给你机会上车",
    rf"{PLAYER_REF}.*(唯一反对|反对.*失败车).*给他上车机会",
    rf"{PLAYER_REF}.*反对.*给他一次上车机会",
    rf"{PLAYER_REF}.*上车吃压力看看态度",
    rf"{PLAYER_REF}.*上轮为什么反对车队",
    rf"问{PLAYER_REF}为什么反对后又失败",
    rf"问{PLAYER_REF}为什么没能维持成功",
    rf"{PLAYER_REF}.*为什么.*排除了{PLAYER_REF}",
    rf"{PLAYER_REF}.*没带{PLAYER_REF}.*为什么",
    rf"为什么没带{PLAYER_REF}",
    rf"为什么把{PLAYER_REF}(留在外面|排在外面|排除在外)",
    rf"为什么没把{PLAYER_REF}放进",
    rf"{PLAYER_REF}.*被排除在车外.*(认可|换人|组队)",
    rf"{PLAYER_REF}.*被排在外.*带.*上车",
    rf"(带|组).{{0,32}}{PLAYER_REF}.{{0,50}}(失败|炸).{{0,16}}{PLAYER_REF}.{{0,10}}解释",
    rf"{PLAYER_REF}.{{0,24}}(失败|炸).{{0,16}}{PLAYER_REF}.{{0,10}}解释",
    rf"{PLAYER_REF}.*给他上车机会看看表现",
    rf"{PLAYER_REF}.*给他机会看看表现",
    rf"{PLAYER_REF}.*解释一下当时为什么反对",
    rf"{PLAYER_REF}.*这次没带你.*该上.*理由",
    rf"{PLAYER_REF}.*任务交给你负责",
    rf"问{PLAYER_REF}为什么接受这个组合",
    rf"{PLAYER_REF}.*对{PLAYER_REF}.*信任程度",
    rf"{PLAYER_REF}.*替{PLAYER_REF}.*(说话|卸压|挡|保)",
    r"保护线|候选关系|不像普通好人互相评价|关系不自然",
)

PRIVATE_CANDIDATE_PATTERN = re.compile(r"候选|保护线|候选关系")
PUBLIC_CANDIDATE_BINDING_PATTERN = re.compile(
    r"(玩家)?\d+(?:/|、|和)(玩家)?\d+.{0,12}(候选|双候选|候选关系|梅林候选)|两个候选.{0,8}玩家\d+和玩家\d+"
)
PAIRWISE_CANDIDATE_TRIAL_PATTERN = re.compile(
    rf"{PLAYER_REF}和{PLAYER_REF}.{{0,24}}(机会证明|证明自己|同时.*观察|一起.*验证|都.*解释|要解释|责任.*担|一起.*扛)"
)
TEMPLATE_PHRASE_PATTERNS = (
    r"责任链清晰",
    r"这条线我先记着",
    r"这条线我(?:暂时)?先?(?:保着|认了|认着|记着)",
    r"后续再看",
    r"先跑一轮",
    r"先走一车",
    r"看看结果",
    r"多一个视角",
    r"多一份视角",
    r"观察一轮",
    r"看看反应",
)
PRIVATE_LEAK_CLAIM_PATTERNS = (
    r"我(?:这边)?没(?:有)?出失败票",
    r"我(?:投|提交)了成功",
    r"我(?:是|一定是)?成功票",
    r"我的票一定是成功票",
    r"(?:我|自己).{0,8}解释(?:自己)?的任务牌",
    r"(?:我|自己).{0,8}交代(?:我自己的)?票向",
)
OVERCONFIDENT_SUCCESS_CLAIM_PATTERNS = (
    r"任务成功.*认这张底牌",
    r"成功.*认这张底牌",
    r"认这张底牌",
    r"打这张任务牌",
)
VERIFICATION_LIKE_TASK_PHRASE_PATTERNS = (
    r"接受检验",
    r"任务理由",
    r"投出失败",
)

DEFAULT_MIN_DECISIONS = 12


def evaluate_prompt_log(path: Path) -> dict[str, Any]:
    sample = json.loads(path.read_text(encoding="utf-8"))
    speeches = sample.get("speeches", [])
    decisions = sample.get("decisions", [])
    public_messages = _public_messages(speeches, decisions)

    identity_actions = [
        message
        for message in public_messages
        if _has_identity_contest_action(str(message.get("message", "")))
    ]
    candidate_relation_pressures = [
        message
        for message in public_messages
        if _has_candidate_relation_pressure(str(message.get("message", "")))
    ]
    public_candidate_bindings = [
        message
        for message in public_messages
        if PUBLIC_CANDIDATE_BINDING_PATTERN.search(str(message.get("message", "")))
    ]
    pairwise_candidate_trial_risks = [
        message
        for message in public_messages
        if PAIRWISE_CANDIDATE_TRIAL_PATTERN.search(str(message.get("message", "")))
    ]
    template_phrases = [
        message
        for message in public_messages
        if _has_template_phrase(str(message.get("message", "")))
    ]
    private_leak_claims = [
        message
        for message in public_messages
        if _has_private_leak_claim(str(message.get("message", "")))
    ]
    overconfident_success_claims = [
        message
        for message in public_messages
        if _has_overconfident_success_claim(str(message.get("message", "")))
    ]
    verification_like_task_phrases = [
        message
        for message in public_messages
        if _has_verification_like_task_phrase(str(message.get("message", "")))
    ]
    private_candidate_mentions = [
        decision
        for decision in decisions
        if _public_message(decision)
        and PRIVATE_CANDIDATE_PATTERN.search(_private_reason(decision))
    ]
    candidate_public_actions = [
        decision
        for decision in private_candidate_mentions
        if _has_candidate_relation_pressure(_public_message(decision))
        and not PAIRWISE_CANDIDATE_TRIAL_PATTERN.search(_public_message(decision))
    ]

    summary = {
        "path": str(path),
        "speech_count": len(speeches),
        "decision_count": len(decisions),
        "identity_contest_action_count": len(identity_actions),
        "candidate_relation_pressure_count": len(candidate_relation_pressures),
        "private_candidate_mention_count": len(private_candidate_mentions),
        "public_candidate_binding_count": len(public_candidate_bindings),
        "pairwise_candidate_trial_risk_count": len(pairwise_candidate_trial_risks),
        "template_phrase_count": len(template_phrases),
        "private_leak_claim_count": len(private_leak_claims),
        "overconfident_success_claim_count": len(overconfident_success_claims),
        "verification_like_task_phrase_count": len(verification_like_task_phrases),
        "private_candidate_decision_count": len(private_candidate_mentions),
        "candidate_public_action_count": len(candidate_public_actions),
        "candidate_public_action_gap_count": len(private_candidate_mentions)
        - len(candidate_public_actions),
        "identity_contest_examples": [
            _speech_summary(speech) for speech in identity_actions[:5]
        ],
        "candidate_relation_pressure_examples": [
            _speech_summary(speech) for speech in candidate_relation_pressures[:5]
        ],
        "public_candidate_binding_examples": [
            _speech_summary(speech) for speech in public_candidate_bindings[:5]
        ],
        "pairwise_candidate_trial_risk_examples": [
            _speech_summary(speech) for speech in pairwise_candidate_trial_risks[:5]
        ],
        "template_phrase_examples": [
            _speech_summary(speech) for speech in template_phrases[:5]
        ],
        "private_leak_claim_examples": [
            _speech_summary(speech) for speech in private_leak_claims[:5]
        ],
        "overconfident_success_claim_examples": [
            _speech_summary(speech) for speech in overconfident_success_claims[:5]
        ],
        "verification_like_task_phrase_examples": [
            _speech_summary(speech) for speech in verification_like_task_phrases[:5]
        ],
        "candidate_public_action_examples": [
            _decision_summary(decision) for decision in candidate_public_actions[:5]
        ],
    }
    summary["quality_gate"] = evaluate_quality_gate(summary)
    return summary


def evaluate_quality_gate(
    summary: dict[str, Any], *, min_decisions: int = DEFAULT_MIN_DECISIONS
) -> dict[str, Any]:
    failures = []
    if int(summary.get("decision_count", 0)) < min_decisions:
        failures.append("sample_too_short")
    if int(summary.get("identity_contest_action_count", 0)) < 1:
        failures.append("missing_public_identity_contest_action")
    if int(summary.get("candidate_relation_pressure_count", 0)) < 1:
        failures.append("missing_candidate_relation_pressure")
    if int(summary.get("candidate_public_action_gap_count", 0)) > 0:
        failures.append("private_candidate_without_public_action")
    if int(summary.get("public_candidate_binding_count", 0)) > 0:
        failures.append("public_candidate_binding_risk")
    if int(summary.get("private_leak_claim_count", 0)) > 0:
        failures.append("private_leak_claim_present")
    if int(summary.get("overconfident_success_claim_count", 0)) > 0:
        failures.append("overconfident_success_claim_present")
    if int(summary.get("pairwise_candidate_trial_risk_count", 0)) > 0:
        failures.append("pairwise_candidate_trial_risk_present")
    if int(summary.get("verification_like_task_phrase_count", 0)) > 0:
        failures.append("verification_like_task_phrase_present")
    if int(summary.get("template_phrase_count", 0)) > 0:
        failures.append("template_phrase_present")
    return {
        "passed": not failures,
        "failures": failures,
        "min_decisions": min_decisions,
    }


def _has_identity_contest_action(message: str) -> bool:
    return any(re.search(pattern, message) for pattern in IDENTITY_ACTION_PATTERNS)


def _has_candidate_relation_pressure(message: str) -> bool:
    return any(
        re.search(pattern, message) for pattern in CANDIDATE_RELATION_PRESSURE_PATTERNS
    )


def _has_template_phrase(message: str) -> bool:
    return any(re.search(pattern, message) for pattern in TEMPLATE_PHRASE_PATTERNS)


def _has_private_leak_claim(message: str) -> bool:
    return any(re.search(pattern, message) for pattern in PRIVATE_LEAK_CLAIM_PATTERNS)


def _has_overconfident_success_claim(message: str) -> bool:
    return any(
        re.search(pattern, message) for pattern in OVERCONFIDENT_SUCCESS_CLAIM_PATTERNS
    )


def _has_verification_like_task_phrase(message: str) -> bool:
    return any(
        re.search(pattern, message)
        for pattern in VERIFICATION_LIKE_TASK_PHRASE_PATTERNS
    )


def _public_messages(
    speeches: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> list[dict[str, str]]:
    messages = [_speech_summary(speech) for speech in speeches]
    seen = {(message["player_id"], message["message"]) for message in messages}
    for decision in decisions:
        message = _public_message(decision)
        if not message:
            continue
        row = {
            "player_id": str(decision.get("player_id", "")),
            "message": message,
        }
        key = (row["player_id"], row["message"])
        if key in seen:
            continue
        seen.add(key)
        messages.append(row)
    return messages


def _private_reason(decision: dict[str, Any]) -> str:
    output = decision.get("output") or {}
    if not isinstance(output, dict):
        return ""
    return str(output.get("private_reason_summary", ""))


def _public_message(decision: dict[str, Any]) -> str:
    output = decision.get("output") or {}
    if not isinstance(output, dict):
        return ""
    return str(output.get("public_message", ""))


def _speech_summary(speech: dict[str, Any]) -> dict[str, str]:
    return {
        "player_id": str(speech.get("player_id", "")),
        "message": str(speech.get("message", "")),
    }


def _decision_summary(decision: dict[str, Any]) -> dict[str, str]:
    return {
        "player_id": str(decision.get("player_id", "")),
        "phase": str(decision.get("phase", "")),
        "decision_type": str(decision.get("decision_type", "")),
        "public_message": _public_message(decision),
        "private_reason_summary": _private_reason(decision),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate SoloAvalon prompt sample logs.")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Exit with status 1 when the quality gate fails.",
    )
    args = parser.parse_args()

    summary = evaluate_prompt_log(args.path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_gate and not summary["quality_gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
