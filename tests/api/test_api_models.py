import unittest

from backend.app.api.models import (
    CreateGameRequest,
    HumanActionRequest,
    LlmProfileRequest,
)


class ApiModelsTests(unittest.TestCase):
    def test_create_game_request_normalizes_optional_fields(self):
        request = CreateGameRequest.from_payload(
            {
                "ai_names": ["A", "B"],
                "default_llm_profile_id": "",
                "ai_profile_overrides": {"player_2": "profile_1"},
            }
        )

        self.assertEqual(request.ai_names, ["A", "B"])
        self.assertIsNone(request.default_llm_profile_id)
        self.assertEqual(request.ai_profile_overrides, {"player_2": "profile_1"})

    def test_create_game_request_ignores_legacy_seed_field(self):
        request = CreateGameRequest.from_payload({"seed": "42"})

        self.assertFalse(hasattr(request, "seed"))

    def test_human_action_request_requires_action_type(self):
        with self.assertRaises(ValueError):
            HumanActionRequest.from_payload({"vote": "approve"})

        request = HumanActionRequest.from_payload(
            {"action_type": "vote", "vote": "approve"}
        )

        self.assertEqual(request.action_type, "vote")
        self.assertEqual(request.payload, {"vote": "approve"})

    def test_llm_profile_request_normalizes_runtime_numbers(self):
        request = LlmProfileRequest.from_payload(
            {
                "name": "DeepSeek",
                "base_url": "https://api.example.com/v1",
                "api_key": "test-api-key",
                "model": "deepseek-chat",
                "temperature": "0.7",
                "timeout": "30",
            }
        )

        self.assertEqual(request.temperature, 0.7)
        self.assertFalse(hasattr(request, "max_tokens"))
        self.assertEqual(request.timeout, 30.0)
