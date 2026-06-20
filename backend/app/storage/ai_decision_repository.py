from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AiDecisionInput:
    game_id: str
    player_id: str
    phase: str
    decision_type: str
    input_summary: str
    strategy_summary: str
    output: dict[str, Any]
    model_name: str
    llm_profile_id: str | None
    prompt_template_name: str
    prompt_template_version: str
    context_builder_version: str
    stable_prefix_hash: str
    cache_strategy: str
    context_summary: str
    context_truncated: bool
    output_raw: str | None
    output_parsed: dict[str, Any] | None
    validation_status: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    cache_hit_rate: float | None = None


@dataclass(frozen=True)
class StoredAiDecision:
    id: int
    game_id: str
    player_id: str
    phase: str
    decision_type: str
    input_summary: str
    strategy_summary: str
    output: dict[str, Any]
    model_name: str
    llm_profile_id: str | None
    prompt_template_name: str
    prompt_template_version: str
    context_builder_version: str
    stable_prefix_hash: str
    cache_strategy: str
    context_summary: str
    context_truncated: bool
    output_raw: str | None
    output_parsed: dict[str, Any] | None
    validation_status: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None
    cache_hit_rate: float | None
    created_at: str


class AiDecisionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def save_decision(self, decision: AiDecisionInput) -> StoredAiDecision:
        created_at = _utc_now()
        cursor = self._connection.execute(
            """
            insert into ai_decisions (
                game_id, player_id, phase, decision_type, input_summary,
                strategy_summary, output, model_name, llm_profile_id,
                prompt_template_name, prompt_template_version, context_builder_version,
                stable_prefix_hash, cache_strategy, context_summary, context_truncated,
                output_raw, output_parsed, validation_status, prompt_tokens,
                completion_tokens, total_tokens, cached_tokens, cache_hit_rate, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.game_id,
                decision.player_id,
                decision.phase,
                decision.decision_type,
                decision.input_summary,
                decision.strategy_summary,
                _dump_json(decision.output),
                decision.model_name,
                decision.llm_profile_id,
                decision.prompt_template_name,
                decision.prompt_template_version,
                decision.context_builder_version,
                decision.stable_prefix_hash,
                decision.cache_strategy,
                decision.context_summary,
                1 if decision.context_truncated else 0,
                decision.output_raw,
                _dump_json(decision.output_parsed) if decision.output_parsed is not None else None,
                decision.validation_status,
                decision.prompt_tokens,
                decision.completion_tokens,
                decision.total_tokens,
                decision.cached_tokens,
                decision.cache_hit_rate,
                created_at,
            ),
        )
        self._connection.commit()
        saved = self.get_decision(int(cursor.lastrowid))
        if saved is None:
            raise RuntimeError("failed to save ai decision")
        return saved

    def get_decision(self, decision_id: int) -> StoredAiDecision | None:
        row = self._connection.execute(
            "select * from ai_decisions where id = ?",
            (decision_id,),
        ).fetchone()
        return _decision_from_row(row) if row is not None else None

    def list_decisions(self, game_id: str) -> list[StoredAiDecision]:
        rows = self._connection.execute(
            "select * from ai_decisions where game_id = ? order by id",
            (game_id,),
        ).fetchall()
        return [_decision_from_row(row) for row in rows]


def _decision_from_row(row: sqlite3.Row) -> StoredAiDecision:
    output_parsed = row["output_parsed"]
    return StoredAiDecision(
        id=row["id"],
        game_id=row["game_id"],
        player_id=row["player_id"],
        phase=row["phase"],
        decision_type=row["decision_type"],
        input_summary=row["input_summary"],
        strategy_summary=row["strategy_summary"],
        output=json.loads(row["output"]),
        model_name=row["model_name"],
        llm_profile_id=row["llm_profile_id"],
        prompt_template_name=row["prompt_template_name"],
        prompt_template_version=row["prompt_template_version"],
        context_builder_version=row["context_builder_version"],
        stable_prefix_hash=row["stable_prefix_hash"],
        cache_strategy=row["cache_strategy"],
        context_summary=row["context_summary"],
        context_truncated=bool(row["context_truncated"]),
        output_raw=row["output_raw"],
        output_parsed=json.loads(output_parsed) if output_parsed is not None else None,
        validation_status=row["validation_status"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        total_tokens=row["total_tokens"],
        cached_tokens=row["cached_tokens"],
        cache_hit_rate=row["cache_hit_rate"],
        created_at=row["created_at"],
    )


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
