import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.api.llm_profiles as llm_profiles_module
from backend.app.api.llm_profiles import LlmProfilesApi, build_llm_profiles_router
from backend.app.llm.provider import LlmCompletionResult, LlmProvider
from backend.app.storage.llm_profile_repository import LlmProfileRepository


class LlmProfilesApiTests(unittest.TestCase):
    def test_profile_api_crud_does_not_return_plain_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)

            created = api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "reasoning_effort": "high",
                    "timeout": 30.0,
                }
            )
            listed = api.list_profiles()

            self.assertNotIn("api_key", created)
            self.assertEqual(created["api_key_masked"], "test********cdef")
            self.assertNotIn("api_key", listed[0])
            self.assertEqual(listed[0]["api_key_masked"], "test********cdef")
            self.assertEqual(listed[0]["reasoning_effort"], "high")
            self.assertNotIn("temperature", listed[0])
            self.assertEqual([profile["id"] for profile in listed], ["profile_1"])

    def test_profile_api_test_is_structural_without_live_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class PassingProvider:
                def chat_completion(self, profile, messages):
                    return LlmCompletionResult(content="pong")

            api = _api(tmpdir, provider=PassingProvider())
            api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "reasoning_effort": "medium",
                    "timeout": 30.0,
                }
            )

            result = api.test_profile("profile_1")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["profile_id"], "profile_1")
            self.assertEqual(result["model"], "deepseek-chat")
            self.assertEqual(result["response_preview"], "pong")

    def test_profile_api_test_reports_provider_failure_without_leaking_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class FailingProvider:
                def chat_completion(self, profile, messages):
                    raise RuntimeError(f"bad key {profile.api_key}")

            api = _api(tmpdir, provider=FailingProvider())
            api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "reasoning_effort": "medium",
                    "timeout": 30.0,
                }
            )

            result = api.test_profile("profile_1")

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["profile_id"], "profile_1")
            self.assertIn("bad key", result["error"])
            self.assertNotIn("test-key-1234567890abcdef", result["error"])

    def test_profile_update_preserves_secret_when_api_key_is_blank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config" / "llm_profiles.json"
            repository = LlmProfileRepository(config_path=config_path)
            api = LlmProfilesApi(repository)
            api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "reasoning_effort": "medium",
                    "timeout": 30.0,
                }
            )

            updated = api.update_profile(
                "profile_1",
                {
                    "name": "Qwen",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "",
                    "model": "qwen-plus",
                    "reasoning_effort": "high",
                    "timeout": 20.0,
                },
            )

            stored = repository.get_profile("profile_1")
            self.assertEqual(updated["name"], "Qwen")
            self.assertEqual(updated["reasoning_effort"], "high")
            self.assertEqual(stored.api_key, "test-key-1234567890abcdef")

    def test_http_crud_and_connectivity_use_real_sdk_with_fake_http(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            requests = []

            def handle(request):
                requests.append(request)
                return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

            repository = LlmProfileRepository(config_path=Path(tmpdir) / "profiles.json")
            provider = LlmProvider(transport=httpx.MockTransport(handle))
            app = FastAPI()
            with patch.object(llm_profiles_module, "LlmProvider", return_value=provider):
                app.include_router(build_llm_profiles_router(repository))

            payload = {
                "id": "profile_1",
                "name": "Custom Provider",
                "base_url": "https://api.example.com/custom/v1",
                "api_key": "test-key-1234567890abcdef",
                "model": "custom-chat",
                "timeout": "30",
            }
            with TestClient(app) as client:
                created = client.post("/api/llm-profiles", json=payload)
                self.assertEqual(created.status_code, 200)
                self.assertNotIn(payload["api_key"], created.text)
                checked = client.post("/api/llm-profiles/profile_1/test")
                self.assertEqual(checked.status_code, 200)
                self.assertEqual(checked.json()["response_preview"], "pong")
                self.assertEqual(checked.json()["status"], "ok")
                self.assertEqual(
                    str(requests[0].url), "https://api.example.com/custom/v1/chat/completions"
                )
                self.assertEqual(requests[0].headers["Authorization"], f"Bearer {payload['api_key']}")

                updated = client.put(
                    "/api/llm-profiles/profile_1",
                    json=payload | {"api_key": "", "model": "updated-model"},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(repository.get_profile("profile_1").api_key, payload["api_key"])
                self.assertEqual(client.get("/api/llm-profiles").json()[0]["model"], "updated-model")
                self.assertEqual(client.delete("/api/llm-profiles/profile_1").status_code, 200)
                self.assertEqual(client.get("/api/llm-profiles").json(), [])

    def test_http_validation_rejects_bad_config_without_leaking_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = LlmProfileRepository(config_path=Path(tmpdir) / "profiles.json")
            app = FastAPI()
            app.include_router(build_llm_profiles_router(repository))
            payload = {
                "id": "invalid",
                "name": "Invalid",
                "base_url": "https://api.example.com/v1",
                "api_key": "test-secret-not-for-errors",
                "model": "custom-chat",
                "timeout": -1,
            }
            with TestClient(app) as client:
                for invalid in (payload, {key: value for key, value in payload.items() if key != "timeout"}):
                    response = client.post("/api/llm-profiles", json=invalid)
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn(payload["api_key"], response.text)

            self.assertEqual(repository.list_profiles(), [])

    def test_route_wrapper_reports_profile_errors_with_traceback(self):
        def fail_with_value_error():
            raise ValueError("unknown llm profile id: missing")

        original_http_exception = llm_profiles_module.HTTPException
        llm_profiles_module.HTTPException = _FakeHTTPException
        try:
            with self.assertRaises(_FakeHTTPException) as captured:
                llm_profiles_module._call(fail_with_value_error)
        finally:
            llm_profiles_module.HTTPException = original_http_exception

        self.assertEqual(captured.exception.status_code, 400)
        self.assertEqual(captured.exception.detail["message"], "unknown llm profile id: missing")
        self.assertEqual(captured.exception.detail["error_type"], "ValueError")
        self.assertIn("ValueError: unknown llm profile id: missing", captured.exception.detail["traceback"])

    def test_route_wrapper_reports_unexpected_profile_errors_with_traceback(self):
        def fail_with_runtime_error():
            raise RuntimeError("profile store unavailable")

        original_http_exception = llm_profiles_module.HTTPException
        llm_profiles_module.HTTPException = _FakeHTTPException
        try:
            with self.assertRaises(_FakeHTTPException) as captured:
                llm_profiles_module._call(fail_with_runtime_error)
        finally:
            llm_profiles_module.HTTPException = original_http_exception

        self.assertEqual(captured.exception.status_code, 500)
        self.assertEqual(captured.exception.detail["message"], "profile store unavailable")
        self.assertEqual(captured.exception.detail["error_type"], "RuntimeError")
        self.assertIn("RuntimeError: profile store unavailable", captured.exception.detail["traceback"])


def _api(tmpdir, provider=None):
    config_path = Path(tmpdir) / "config" / "llm_profiles.json"
    return LlmProfilesApi(LlmProfileRepository(config_path=config_path), provider=provider)


class _FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
