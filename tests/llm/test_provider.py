import json
import unittest
from unittest.mock import patch

import httpx
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError

from backend.app.ai.player import AiDecisionError, AiPlayer
from backend.app.game.rules import create_five_player_game
from backend.app.llm.provider import EmptyModelOutputError, LlmProvider
from tests.ai.test_prompting_and_provider import test_profile


def completion_response(content="{}", usage=None):
    payload = {
        "id": "test-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return httpx.Response(200, json=payload)


class LlmProviderTests(unittest.TestCase):
    def test_sdk_sends_custom_endpoint_credentials_payload_and_timeout(self):
        requests = []

        def handle(request):
            requests.append(request)
            return completion_response('{"vote":"approve"}')

        result = LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(result.content), {"vote": "approve"})
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.example.com/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer test-api-key")
        self.assertEqual(
            json.loads(request.content),
            {
                "model": "model",
                "messages": [{"role": "user", "content": "vote"}],
                "reasoning_effort": "high",
            },
        )
        self.assertEqual(request.extensions["timeout"]["read"], 5.0)
        self.assertEqual(len(requests), 1)

    def test_sdk_preserves_full_endpoint_and_custom_prefix_profiles(self):
        for base_url, expected in (
            ("https://api.example.com/v1/", "https://api.example.com/v1/chat/completions"),
            ("https://api.example.com/v1/chat/completions", "https://api.example.com/v1/chat/completions"),
            ("http://localhost:1234/custom/", "http://localhost:1234/custom/chat/completions"),
        ):
            with self.subTest(base_url=base_url):
                requests = []

                def handle(request):
                    requests.append(request)
                    return completion_response()

                profile = test_profile().model_copy(update={"base_url": base_url})
                LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(profile, [])
                self.assertEqual(str(requests[0].url), expected)

    def test_sdk_response_usage_is_mapped_to_project_contract(self):
        transport = httpx.MockTransport(
            lambda request: completion_response(
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "total_tokens": 125,
                    "prompt_tokens_details": {"cached_tokens": 40},
                }
            )
        )

        result = LlmProvider(transport=transport).chat_completion(test_profile(), [])

        self.assertEqual(result.prompt_tokens, 100)
        self.assertEqual(result.completion_tokens, 25)
        self.assertEqual(result.total_tokens, 125)
        self.assertEqual(result.cached_tokens, 40)
        self.assertEqual(result.cache_hit_rate, 0.4)

    def test_sdk_response_without_usage_is_supported(self):
        result = LlmProvider(
            transport=httpx.MockTransport(lambda request: completion_response())
        ).chat_completion(test_profile(), [])

        self.assertEqual(result.content, "{}")
        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)
        self.assertIsNone(result.total_tokens)
        self.assertIsNone(result.cached_tokens)
        self.assertIsNone(result.cache_hit_rate)

    def test_invalid_url_is_rejected_before_sending(self):
        requests = []
        profile = test_profile().model_copy(update={"base_url": "file:///v1"})
        provider = LlmProvider(
            transport=httpx.MockTransport(lambda request: requests.append(request))
        )

        with self.assertRaisesRegex(ValueError, "base_url must start with http:// or https://"):
            provider.chat_completion(profile, [])

        self.assertEqual(requests, [])

    def test_sdk_retries_transport_failures_until_success(self):
        for exception_type in (httpx.ReadTimeout, httpx.ConnectError):
            with self.subTest(exception_type=exception_type):
                attempts = []

                def handle(request):
                    attempts.append(request)
                    if len(attempts) < 3:
                        raise exception_type("test connection failure", request=request)
                    return completion_response()

                with patch("time.sleep"):
                    result = LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(
                        test_profile(), []
                    )

                self.assertEqual(result.content, "{}")
                self.assertEqual(len(attempts), 3)

    def test_sdk_stops_after_configured_retries(self):
        for cause, expected_error in (
            (httpx.ReadTimeout, APITimeoutError),
            (httpx.ConnectError, APIConnectionError),
        ):
            with self.subTest(cause=cause):
                attempts = []

                def handle(request):
                    attempts.append(request)
                    raise cause("test connection failure", request=request)

                profile = test_profile().model_copy(update={"timeout_retries": 2})
                with patch("time.sleep"), self.assertRaises(expected_error):
                    LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(profile, [])

                self.assertEqual(len(attempts), 3)

    def test_sdk_retries_retryable_http_statuses(self):
        for status in (408, 409, 429, 500):
            with self.subTest(status=status):
                attempts = []

                def handle(request):
                    attempts.append(request)
                    if len(attempts) == 1:
                        return httpx.Response(status, json={"error": {"message": "try again"}})
                    return completion_response()

                with patch("time.sleep"):
                    result = LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(
                        test_profile(), []
                    )

                self.assertEqual(result.content, "{}")
                self.assertEqual(len(attempts), 2)

    def test_sdk_does_not_retry_authentication_errors(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})

        with self.assertRaises(AuthenticationError) as captured:
            LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(test_profile(), [])

        self.assertEqual(captured.exception.status_code, 401)
        self.assertEqual(len(requests), 1)

    def test_sdk_network_failure_does_not_repeat_ai_output_retry_loop(self):
        attempts = []

        def handle(request):
            attempts.append(request)
            raise httpx.ReadTimeout("test timeout", request=request)

        provider = LlmProvider(transport=httpx.MockTransport(handle))
        state = create_five_player_game(seed=21)
        profile = test_profile().model_copy(update={"timeout_retries": 2})
        with patch("time.sleep"), self.assertRaises(AiDecisionError) as captured:
            AiPlayer(provider=provider).propose_team(state, state.players[0].id, profile)

        self.assertEqual(captured.exception.error_type, "APITimeoutError")
        self.assertEqual(len(attempts), 3)

    def test_zero_retries_disables_sdk_http_retries(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(429, json={"error": {"message": "rate limited"}})

        profile = test_profile().model_copy(update={"timeout_retries": 0})
        with self.assertRaises(RateLimitError):
            LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(profile, [])

        self.assertEqual(len(requests), 1)

    def test_empty_output_is_left_for_ai_output_retries(self):
        requests = []

        def handle(request):
            requests.append(request)
            return completion_response(" ")

        with self.assertRaises(EmptyModelOutputError):
            LlmProvider(transport=httpx.MockTransport(handle)).chat_completion(test_profile(), [])

        self.assertEqual(len(requests), 1)

    def test_missing_choices_or_content_are_reported(self):
        for payload, expected in (
            ({"choices": []}, "did not include choices"),
            ({"choices": [{"message": {"content": None}}]}, "did not include message content"),
        ):
            with self.subTest(payload=payload):
                transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
                with self.assertRaisesRegex(ValueError, expected):
                    LlmProvider(transport=transport).chat_completion(test_profile(), [])

    def test_http_transport_is_closed_after_call(self):
        class TrackingTransport(httpx.MockTransport):
            closed = False

            def close(self):
                self.closed = True
                super().close()

        for response in (
            lambda request: completion_response(),
            lambda request: httpx.Response(401, json={"error": {"message": "unauthorized"}}),
        ):
            with self.subTest(response=response):
                transport = TrackingTransport(response)
                try:
                    LlmProvider(transport=transport).chat_completion(test_profile(), [])
                except AuthenticationError:
                    pass
                self.assertTrue(transport.closed)
