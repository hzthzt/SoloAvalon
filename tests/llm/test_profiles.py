import unittest

from backend.app.llm.profiles import LlmProfile, LlmProfileConfig, LlmProfileInput, mask_api_key


def _profile_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {
        "id": "profile_1",
        "name": "DeepSeek",
        "base_url": "https://api.example.com/v1",
        "api_key": "test-key-1234567890abcdef",
        "model": "deepseek-chat",
        "reasoning_effort": "high",
        "timeout": 30.0,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    return kwargs


def _profile_input_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "name": "Bad",
        "base_url": "https://api.example.com/v1",
        "api_key": "secret",
        "model": "model",
        "reasoning_effort": "medium",
        "timeout": 30.0,
    }
    kwargs.update(overrides)
    return kwargs


class LlmProfileTests(unittest.TestCase):
    def test_mask_api_key_hides_full_secret(self):
        api_key = "test-key-1234567890abcdef"
        masked = mask_api_key(api_key)

        self.assertEqual(masked, "test********cdef")
        self.assertEqual(mask_api_key("short"), "*****")
        self.assertEqual(mask_api_key("123456789012345"), "***************")
        self.assertEqual(mask_api_key(""), "")

    def test_profile_types_exclude_removed_runtime_fields(self):
        self.assertNotIn("max_tokens", LlmProfileInput.model_fields)
        self.assertNotIn("max_tokens", LlmProfile.model_fields)
        self.assertNotIn("temperature", LlmProfileInput.model_fields)
        self.assertNotIn("temperature", LlmProfile.model_fields)

    def test_public_dict_excludes_plain_api_key(self):
        profile = LlmProfile(**_profile_kwargs())

        public_dict = profile.to_public_dict()

        self.assertNotIn("api_key", public_dict)
        self.assertEqual(public_dict["api_key_masked"], "test********cdef")
        self.assertEqual(public_dict["reasoning_effort"], "high")
        self.assertNotIn("temperature", public_dict)
        self.assertEqual(public_dict["timeout_retries"], 5)
        self.assertNotIn("max_tokens", public_dict)

    def test_profiles_default_to_five_timeout_retries(self):
        profile_input = LlmProfileInput(**_profile_input_kwargs())
        profile = LlmProfile(**_profile_kwargs())

        self.assertEqual(profile_input.timeout_retries, 5)
        self.assertEqual(profile.timeout_retries, 5)

    def test_input_validation_rejects_invalid_runtime_values(self):
        with self.assertRaises(ValueError):
            LlmProfileInput(**_profile_input_kwargs(reasoning_effort="extreme"))
        with self.assertRaises(ValueError):
            LlmProfileInput(**_profile_input_kwargs(timeout_retries=-1))

    def test_api_and_file_profiles_share_validation(self):
        for overrides in (
            {"name": " "},
            {"api_key": ""},
            {"model": " "},
            {"timeout": 0},
            {"timeout": float("inf")},
            {"timeout": float("nan")},
            {"timeout_retries": 1.5},
            {"timeout_retries": -1},
            {"reasoning_effort": "extreme"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    LlmProfileInput(**_profile_input_kwargs(**overrides))
                with self.assertRaises(ValueError):
                    LlmProfileConfig.model_validate({"profiles": [_profile_kwargs() | overrides]})

    def test_validation_errors_and_repr_do_not_include_api_key(self):
        secret = "test-secret-not-for-errors"
        with self.assertRaises(ValueError) as captured:
            LlmProfileInput(**_profile_input_kwargs(api_key=secret, timeout=-1))

        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(secret, repr(LlmProfileInput(**_profile_input_kwargs(api_key=secret))))

    def test_input_validation_rejects_non_http_base_url(self):
        for base_url in ("api.example.com/v1", "file:///v1"):
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(ValueError, "base_url must start with http:// or https://"):
                    LlmProfileInput(**_profile_input_kwargs(base_url=base_url))

                profile_kwargs = _profile_kwargs() | {"base_url": base_url}
                with self.assertRaisesRegex(ValueError, "base_url must start with http:// or https://"):
                    LlmProfile(**profile_kwargs)

    def test_unconfigured_profile_allows_empty_base_url(self):
        profile = LlmProfile(
            id="unconfigured",
            name="Unconfigured",
            base_url="",
            api_key="",
            model="unconfigured",
            reasoning_effort="none",
            timeout=1.0,
            created_at="",
            updated_at="",
        )

        self.assertEqual(profile.base_url, "")
