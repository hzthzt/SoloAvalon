from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.llm.profiles import LlmProfile, LlmProfileInput


class LlmProfileRepository:
    def __init__(self, connection: sqlite3.Connection, config_path: str | Path | None = None):
        self._connection = connection
        self._config_path = Path(config_path) if config_path is not None else _default_config_path()

    def create_profile(
        self,
        profile_id: str,
        profile_input: LlmProfileInput,
    ) -> LlmProfile:
        now = _utc_now()
        profiles = self._load_profiles()
        if any(profile.id == profile_id for profile in profiles):
            raise ValueError(f"llm profile already exists: {profile_id}")
        profile = LlmProfile(
            id=profile_id,
            name=profile_input.name,
            base_url=profile_input.base_url,
            api_key=profile_input.api_key,
            model=profile_input.model,
            temperature=profile_input.temperature,
            timeout=profile_input.timeout,
            created_at=now,
            updated_at=now,
        )
        self._save_profiles([profile, *profiles])
        return profile

    def get_profile(self, profile_id: str) -> LlmProfile | None:
        for profile in self._load_profiles():
            if profile.id == profile_id:
                return profile
        return None

    def list_profiles(self) -> list[LlmProfile]:
        return sorted(
            self._load_profiles(),
            key=lambda profile: profile.created_at,
            reverse=True,
        )

    def update_profile(
        self,
        profile_id: str,
        profile_input: LlmProfileInput,
    ) -> LlmProfile:
        now = _utc_now()
        profiles = self._load_profiles()
        updated_profile: LlmProfile | None = None
        next_profiles: list[LlmProfile] = []
        for profile in profiles:
            if profile.id != profile_id:
                next_profiles.append(profile)
                continue
            updated_profile = LlmProfile(
                id=profile_id,
                name=profile_input.name,
                base_url=profile_input.base_url,
                api_key=profile_input.api_key,
                model=profile_input.model,
                temperature=profile_input.temperature,
                timeout=profile_input.timeout,
                created_at=profile.created_at,
                updated_at=now,
            )
            next_profiles.append(updated_profile)
        if updated_profile is None:
            raise ValueError(f"unknown llm profile id: {profile_id}")
        self._save_profiles(next_profiles)
        return updated_profile

    def delete_profile(self, profile_id: str) -> None:
        profiles = [profile for profile in self._load_profiles() if profile.id != profile_id]
        self._save_profiles(profiles)

    def resolve_profile_for_player(
        self,
        game_id: str,
        player_id: str,
    ) -> LlmProfile:
        row = self._connection.execute(
            """
            select players.llm_profile_id as player_profile_id,
                   games.default_llm_profile_id as default_profile_id
            from players
            join games on games.id = players.game_id
            where players.game_id = ? and players.id = ?
            """,
            (game_id, player_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown player id for game: {game_id}/{player_id}")

        profile_id = row["player_profile_id"] or row["default_profile_id"]
        if profile_id is None:
            raise ValueError(f"no llm profile configured for player: {game_id}/{player_id}")

        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"configured llm profile does not exist: {profile_id}")
        return profile

    def _load_profiles(self) -> list[LlmProfile]:
        if not self._config_path.exists():
            return []
        raw_text = self._config_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return []
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid llm profile config file: {self._config_path}") from exc
        profile_payloads = payload.get("profiles") if isinstance(payload, dict) else None
        if not isinstance(profile_payloads, list):
            raise ValueError(f"invalid llm profile config file: {self._config_path}")
        return [_profile_from_payload(profile_payload) for profile_payload in profile_payloads]

    def _save_profiles(self, profiles: list[LlmProfile]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [_profile_to_payload(profile) for profile in profiles],
        }
        temp_path = self._config_path.with_name(f"{self._config_path.name}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self._config_path)


def _profile_from_payload(payload: Any) -> LlmProfile:
    if not isinstance(payload, dict):
        raise ValueError("invalid llm profile entry")
    return LlmProfile(
        id=_required_string(payload, "id"),
        name=_required_string(payload, "name"),
        base_url=_required_string(payload, "base_url"),
        api_key=_required_string(payload, "api_key"),
        model=_required_string(payload, "model"),
        temperature=float(payload["temperature"]),
        timeout=float(payload["timeout"]),
        created_at=_required_string(payload, "created_at"),
        updated_at=_required_string(payload, "updated_at"),
    )


def _profile_to_payload(profile: LlmProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "base_url": profile.base_url,
        "api_key": profile.api_key,
        "model": profile.model,
        "temperature": profile.temperature,
        "timeout": profile.timeout,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid llm profile config field: {key}")
    return value


def _default_config_path() -> Path:
    configured_path = os.environ.get("SOLOAVALON_LLM_CONFIG")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parents[3] / "config" / "llm_profiles.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
