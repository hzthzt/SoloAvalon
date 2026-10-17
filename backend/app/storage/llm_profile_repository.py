from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from backend.app.llm.profiles import LlmProfile, LlmProfileConfig, LlmProfileInput


class LlmProfileRepository:
    def __init__(self, config_path: str | Path | None = None):
        self._config_path = Path(config_path) if config_path is not None else _default_config_path()

    def create_profile(
        self,
        profile_id: str,
        profile_input: LlmProfileInput,
    ) -> LlmProfile:
        now = _utc_now()
        profiles = self._load_profiles()
        profile = LlmProfile(
            **profile_input.model_dump(),
            id=profile_id,
            created_at=now,
            updated_at=now,
        )
        if any(existing.id == profile.id for existing in profiles):
            raise ValueError(f"llm profile already exists: {profile.id}")
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
                **profile_input.model_dump(),
                id=profile_id,
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

    def _load_profiles(self) -> list[LlmProfile]:
        if not self._config_path.exists():
            return []
        raw_text = self._config_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return []
        try:
            return LlmProfileConfig.model_validate_json(raw_text).profiles
        except ValidationError as exc:
            raise ValueError(f"invalid llm profile config file: {self._config_path}") from exc

    def _save_profiles(self, profiles: list[LlmProfile]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        config = LlmProfileConfig(profiles=profiles)
        temp_path = self._config_path.with_name(f"{self._config_path.name}.tmp")
        temp_path.write_text(
            config.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self._config_path)


def _default_config_path() -> Path:
    configured_path = os.environ.get("SOLOAVALON_LLM_CONFIG")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parents[3] / "config" / "llm_profiles.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
