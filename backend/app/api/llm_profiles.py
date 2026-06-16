from __future__ import annotations

from typing import Any

from backend.app.llm.provider import LlmProvider
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from .models import LlmProfileRequest

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    APIRouter = None
    HTTPException = None


class LlmProfilesApi:
    def __init__(self, repository: LlmProfileRepository, provider: LlmProvider | Any | None = None):
        self._repository = repository
        self._provider = provider or LlmProvider()

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.to_public_dict() for profile in self._repository.list_profiles()]

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(payload["id"])
        profile = self._repository.create_profile(
            profile_id,
            LlmProfileRequest.from_payload(payload).to_input(),
        )
        return profile.to_public_dict()

    def update_profile(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self._repository.get_profile(profile_id)
        if existing is None:
            raise ValueError(f"unknown llm profile id: {profile_id}")
        update_payload = dict(payload)
        if not str(update_payload.get("api_key", "")).strip():
            update_payload["api_key"] = existing.api_key
        profile = self._repository.update_profile(
            profile_id,
            LlmProfileRequest.from_payload(update_payload).to_input(),
        )
        return profile.to_public_dict()

    def delete_profile(self, profile_id: str) -> dict[str, str]:
        self._repository.delete_profile(profile_id)
        return {"status": "deleted"}

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self._repository.get_profile(profile_id)
        if profile is None:
            raise ValueError(f"unknown llm profile id: {profile_id}")
        messages = [
            {
                "role": "system",
                "content": "Return a short plain-text readiness response.",
            },
            {
                "role": "user",
                "content": "SoloAvalon model profile connectivity check.",
            },
        ]
        try:
            content = self._provider.chat_completion(profile, messages)
        except Exception as exc:
            return {
                "status": "failed",
                "profile_id": profile.id,
                "model": profile.model,
                "base_url": profile.base_url,
                "error": _mask_secret(str(exc), profile.api_key),
            }
        return {
            "status": "ok",
            "profile_id": profile.id,
            "model": profile.model,
            "base_url": profile.base_url,
            "response_preview": content[:160],
        }


def build_llm_profiles_router(repository: LlmProfileRepository):
    if APIRouter is None:
        return None
    api = LlmProfilesApi(repository)
    router = APIRouter(prefix="/api/llm-profiles", tags=["llm-profiles"])

    @router.get("")
    def list_profiles():
        return _call(api.list_profiles)

    @router.post("")
    def create_profile(payload: dict[str, Any]):
        return _call(lambda: api.create_profile(payload))

    @router.put("/{profile_id}")
    def update_profile(profile_id: str, payload: dict[str, Any]):
        return _call(lambda: api.update_profile(profile_id, payload))

    @router.delete("/{profile_id}")
    def delete_profile(profile_id: str):
        return _call(lambda: api.delete_profile(profile_id))

    @router.post("/{profile_id}/test")
    def test_profile(profile_id: str):
        return _call(lambda: api.test_profile(profile_id))

    return router

def _call(handler):
    try:
        return handler()
    except ValueError as exc:
        if HTTPException is None:
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mask_secret(message: str, secret: str) -> str:
    if not secret:
        return message
    return message.replace(secret, "[redacted]")
