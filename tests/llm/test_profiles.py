import unittest
from dataclasses import fields

from backend.app.llm.profiles import LlmProfile, LlmProfileInput, mask_api_key


def _profile_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {
        "id": "profile_1",
        "name": "DeepSeek",
        "base_url": "https://api.example.com/v1",
        "api_key": "test-key-1234567890abcdef",
        "model": "deepseek-chat",
        "temperature": 0.7,
        "timeout": 30.0,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    if "max_tokens" in {field.name for field in fields(LlmProfile)}:
        kwargs["max_tokens"] = 1024
    return kwargs


def _profile_input_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "name": "Bad",
        "base_url": "https://api.example.com/v1",
        "api_key": "secret",
        "model": "model",
        "temperature": 0.5,
        "timeout": 30.0,
    }
    if "max_tokens" in {field.name for field in fields(LlmProfileInput)}:
        kwargs["max_tokens"] = 1024
    kwargs.update(overrides)
    return kwargs


class LlmProfileTests(unittest.TestCase):
    def test_mask_api_key_hides_full_secret(self):
        self.assertEqual(mask_api_key("test-key-1234567890abcdef"), "test...cdef")
        self.assertEqual(mask_api_key("short"), "*****")
        self.assertEqual(mask_api_key(""), "")

    def test_profile_types_do_not_include_user_configured_max_tokens(self):
        self.assertNotIn("max_tokens", {field.name for field in fields(LlmProfileInput)})
        self.assertNotIn("max_tokens", {field.name for field in fields(LlmProfile)})

    def test_public_dict_includes_plain_api_key_for_local_config_ui(self):
        profile = LlmProfile(**_profile_kwargs())

        public_dict = profile.to_public_dict()

        self.assertEqual(public_dict["api_key"], "test-key-1234567890abcdef")
        self.assertEqual(public_dict["api_key_masked"], "test...cdef")
        self.assertEqual(public_dict["timeout_retries"], 5)
        self.assertNotIn("max_tokens", public_dict)

    def test_profiles_default_to_five_timeout_retries(self):
        profile_input = LlmProfileInput(**_profile_input_kwargs())
        profile = LlmProfile(**_profile_kwargs())

        self.assertEqual(profile_input.timeout_retries, 5)
        self.assertEqual(profile.timeout_retries, 5)

    def test_input_validation_rejects_invalid_runtime_values(self):
        with self.assertRaises(ValueError):
            LlmProfileInput(**_profile_input_kwargs(temperature=-0.1))
        with self.assertRaises(ValueError):
            LlmProfileInput(**_profile_input_kwargs(timeout_retries=-1))
