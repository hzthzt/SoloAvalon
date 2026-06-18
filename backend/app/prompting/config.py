from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromptTemplateConfig:
    version: str
    section_titles: dict[str, str]
    system_lines: list[str]
    labels: dict[str, str]
    role_labels: dict[str, str]
    faction_labels: dict[str, str]
    vote_labels: dict[str, str]
    quest_result_labels: dict[str, str]
    role_descriptions: dict[str, str]
    role_gameplay: dict[str, str]
    role_strategy_tips: dict[str, str]
    role_tip_detail: dict[str, Any]
    extra_information: dict[str, str]
    recommended_role_setups: dict[int, dict[str, list[str]]]
    optional_mechanics: dict[str, dict[str, Any]]
    event_templates: dict[str, str]
    action_prompts: dict[str, dict[str, Any]]
    source_path: Path


def load_prompt_template_config(path: str | Path | None = None) -> PromptTemplateConfig:
    config_path = Path(path) if path is not None else _default_config_path()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"prompt template config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid prompt template config file: {config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid prompt template config file: {config_path}")
    return PromptTemplateConfig(
        version=_required_string(payload, "version"),
        section_titles=_required_string_dict(payload, "section_titles"),
        system_lines=_required_string_list(payload, "system_lines"),
        labels=_required_string_dict(payload, "labels"),
        role_labels=_required_string_dict(payload, "role_labels"),
        faction_labels=_required_string_dict(payload, "faction_labels"),
        vote_labels=_required_string_dict(payload, "vote_labels"),
        quest_result_labels=_required_string_dict(payload, "quest_result_labels"),
        role_descriptions=_required_string_dict(payload, "role_descriptions"),
        role_gameplay=_required_string_dict(payload, "role_gameplay"),
        role_strategy_tips=_required_string_dict(payload, "role_strategy_tips"),
        role_tip_detail=_optional_role_tip_detail(payload),
        extra_information=_required_string_dict(payload, "extra_information"),
        recommended_role_setups=_required_role_setups(payload),
        optional_mechanics=_required_optional_mechanics(payload),
        event_templates=_required_string_dict(payload, "event_templates"),
        action_prompts=_required_action_prompts(payload),
        source_path=config_path,
    )


def _default_config_path() -> Path:
    configured_path = os.environ.get("SOLOAVALON_PROMPT_CONFIG")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parents[3] / "config.example" / "prompt_templates.json"


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid prompt template config field: {key}")
    return value


def _required_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"invalid prompt template config field: {key}")
    return list(value)


def _required_string_dict(payload: dict[str, Any], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(item_key, str) and isinstance(item_value, str)
        for item_key, item_value in value.items()
    ):
        raise ValueError(f"invalid prompt template config field: {key}")
    return dict(value)


def _required_role_setups(payload: dict[str, Any]) -> dict[int, dict[str, list[str]]]:
    value = payload.get("recommended_role_setups")
    if not isinstance(value, dict):
        raise ValueError("invalid prompt template config field: recommended_role_setups")
    setups: dict[int, dict[str, list[str]]] = {}
    for player_count_text, setup in value.items():
        try:
            player_count = int(player_count_text)
        except ValueError as exc:
            raise ValueError("invalid prompt template recommended role setup key") from exc
        if not isinstance(setup, dict):
            raise ValueError("invalid prompt template recommended role setup")
        good_roles = setup.get("good")
        evil_roles = setup.get("evil")
        if not _is_string_list(good_roles) or not _is_string_list(evil_roles):
            raise ValueError("invalid prompt template recommended role setup roles")
        setups[player_count] = {
            "good": list(good_roles),
            "evil": list(evil_roles),
        }
    return setups


def _optional_role_tip_detail(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("role_tip_detail")
    if value is None:
        return {"enabled": False, "label": "详细身份提示", "role_tips": {}}
    if not isinstance(value, dict):
        raise ValueError("invalid prompt template config field: role_tip_detail")
    enabled = value.get("enabled", False)
    label = value.get("label", "详细身份提示")
    role_tips = value.get("role_tips", {})
    if not isinstance(enabled, bool):
        raise ValueError("invalid prompt template role tip detail toggle")
    if not isinstance(label, str) or not label:
        raise ValueError("invalid prompt template role tip detail label")
    if not isinstance(role_tips, dict):
        raise ValueError("invalid prompt template role tip detail tips")
    parsed_role_tips: dict[str, list[str]] = {}
    for role, tips in role_tips.items():
        if not isinstance(role, str) or not _is_string_list(tips):
            raise ValueError("invalid prompt template role tip detail role tips")
        parsed_role_tips[role] = list(tips)
    return {
        "enabled": enabled,
        "label": label,
        "role_tips": parsed_role_tips,
    }


def _required_optional_mechanics(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = payload.get("optional_mechanics")
    if not isinstance(value, dict):
        raise ValueError("invalid prompt template config field: optional_mechanics")
    mechanics: dict[str, dict[str, Any]] = {}
    for mechanic_id, mechanic in value.items():
        if not isinstance(mechanic_id, str) or not isinstance(mechanic, dict):
            raise ValueError("invalid prompt template optional mechanic")
        label = mechanic.get("label")
        description = mechanic.get("description")
        enabled = mechanic.get("enabled")
        default_counts = mechanic.get("default_enabled_for_player_counts", [])
        if not isinstance(label, str) or not isinstance(description, str):
            raise ValueError(f"invalid prompt template optional mechanic text: {mechanic_id}")
        if not isinstance(enabled, bool):
            raise ValueError(f"invalid prompt template optional mechanic toggle: {mechanic_id}")
        if not isinstance(default_counts, list) or not all(
            isinstance(count, int) for count in default_counts
        ):
            raise ValueError(f"invalid prompt template optional mechanic counts: {mechanic_id}")
        mechanics[mechanic_id] = {
            "label": label,
            "description": description,
            "enabled": enabled,
            "default_enabled_for_player_counts": list(default_counts),
        }
    return mechanics


def _required_action_prompts(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = payload.get("action_prompts")
    if not isinstance(value, dict):
        raise ValueError("invalid prompt template config field: action_prompts")
    prompts: dict[str, dict[str, Any]] = {}
    for action, prompt in value.items():
        if not isinstance(action, str) or not isinstance(prompt, dict):
            raise ValueError("invalid prompt template action prompt")
        lines = prompt.get("lines")
        json_contract = prompt.get("json")
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise ValueError(f"invalid prompt template action lines: {action}")
        if not isinstance(json_contract, str):
            raise ValueError(f"invalid prompt template action json: {action}")
        prompts[action] = {"lines": list(lines), "json": json_contract}
    return prompts


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
