import tempfile
import unittest
from pathlib import Path

from backend.app.api.llm_profiles import LlmProfilesApi
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.llm_profile_repository import LlmProfileRepository


class LlmProfilesApiTests(unittest.TestCase):
    def test_profile_api_crud_masks_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = _api(tmpdir)

            created = api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "temperature": 0.7,
                    "timeout": 30.0,
                }
            )
            listed = api.list_profiles()

            self.assertEqual(created["api_key_masked"], "test...cdef")
            self.assertNotIn("api_key", created)
            self.assertEqual([profile["id"] for profile in listed], ["profile_1"])

    def test_profile_api_test_is_structural_without_live_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            class PassingProvider:
                def chat_completion(self, profile, messages):
                    return "pong"

            api = _api(tmpdir, provider=PassingProvider())
            api.create_profile(
                {
                    "id": "profile_1",
                    "name": "DeepSeek",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "test-key-1234567890abcdef",
                    "model": "deepseek-chat",
                    "temperature": 0.7,
                    "timeout": 30.0,
                }
            )

            result = api.test_profile("profile_1")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["profile_id"], "profile_1")
            self.assertEqual(result["model"], "deepseek-chat")

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
                    "temperature": 0.7,
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
            connection = connect_sqlite(":memory:")
            try:
                initialize_database(connection)
                config_path = Path(tmpdir) / "config" / "llm_profiles.json"
                repository = LlmProfileRepository(connection, config_path=config_path)
                api = LlmProfilesApi(repository)
                api.create_profile(
                    {
                        "id": "profile_1",
                        "name": "DeepSeek",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "test-key-1234567890abcdef",
                        "model": "deepseek-chat",
                        "temperature": 0.7,
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
                        "temperature": 0.4,
                        "timeout": 20.0,
                    },
                )

                stored = repository.get_profile("profile_1")
                self.assertEqual(updated["name"], "Qwen")
                self.assertEqual(stored.api_key, "test-key-1234567890abcdef")
            finally:
                connection.close()


def _api(tmpdir, provider=None):
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    config_path = Path(tmpdir) / "config" / "llm_profiles.json"
    return LlmProfilesApi(LlmProfileRepository(connection, config_path=config_path), provider=provider)
