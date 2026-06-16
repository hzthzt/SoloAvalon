import json
import unittest
from dataclasses import fields

from backend.app.ai.context import ContextBuilder
from backend.app.ai.player import AiPlayer, AiTurnResult
from backend.app.game.models import MissionAction, Phase, Role, Vote
from backend.app.game.rules import create_five_player_game, propose_team
from backend.app.llm.profiles import LlmProfile
from backend.app.llm.provider import LlmProvider
from backend.app.prompting.schemas import parse_json_object
from backend.app.prompting.templates import PromptBuilder


def test_profile() -> LlmProfile:
    kwargs = {
        "id": "profile_1",
        "name": "Test",
        "base_url": "https://api.example.com/v1",
        "api_key": "test-api-key",
        "model": "model",
        "temperature": 0.3,
        "timeout": 5.0,
        "created_at": "2026-06-15T00:00:00Z",
        "updated_at": "2026-06-15T00:00:00Z",
    }
    if "max_tokens" in {field.name for field in fields(LlmProfile)}:
        kwargs["max_tokens"] = 256
    return LlmProfile(**kwargs)


class PromptingAndProviderTests(unittest.TestCase):
    def test_prompt_builder_uses_stable_prefix_before_dynamic_suffix(self):
        state = create_five_player_game(seed=20)
        context = ContextBuilder().build(state, state.players[1].id, Phase.SPEECH)

        messages = PromptBuilder().build_messages(context, Phase.SPEECH)

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(context.stable_prefix, messages[0]["content"])
        self.assertIn(context.dynamic_private_suffix, messages[1]["content"])
        self.assertNotIn(context.dynamic_private_suffix, messages[0]["content"])

    def test_parse_json_object_accepts_code_fenced_json(self):
        parsed = parse_json_object('```json\n{"vote":"approve"}\n```')

        self.assertEqual(parsed, {"vote": "approve"})

    def test_llm_provider_uses_injectable_transport(self):
        captured = {}

        def transport(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {
                "choices": [
                    {"message": {"content": json.dumps({"vote": "approve"})}}
                ]
            }

        provider = LlmProvider(transport=transport)

        content = provider.chat_completion(
            test_profile(),
            messages=[{"role": "user", "content": "vote"}],
        )

        self.assertEqual(json.loads(content), {"vote": "approve"})
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-api-key")
        self.assertEqual(captured["payload"]["model"], "model")
        self.assertNotIn("max_tokens", captured["payload"])

    def test_ai_player_falls_back_when_model_returns_illegal_vote(self):
        class BadProvider:
            def chat_completion(self, profile, messages):
                return json.dumps({"vote": "maybe"})

        state = create_five_player_game(seed=21)
        state = propose_team(state, state.players[0].id, ("player_1", "player_2"))
        voter = state.players[3]

        result = AiPlayer(provider=BadProvider()).vote(state, voter.id, test_profile())

        self.assertIsInstance(result, AiTurnResult)
        self.assertEqual(result.validation_status, "fallback")
        self.assertEqual(result.decision.vote, Vote.REJECT)

    def test_ai_player_falls_back_when_good_player_model_returns_fail(self):
        class BadProvider:
            def chat_completion(self, profile, messages):
                return json.dumps(
                    {
                        "mission_action": "fail",
                        "private_reason_summary": "bad output",
                    }
                )

        state = create_five_player_game(seed=22)
        good_player = next(player for player in state.players if player.role == Role.LOYAL_SERVANT)
        teammate = next(player for player in state.players if player.id != good_player.id)
        state = propose_team(state, state.players[0].id, (good_player.id, teammate.id))

        result = AiPlayer(provider=BadProvider()).mission_action(
            state,
            good_player.id,
            test_profile(),
        )

        self.assertEqual(result.validation_status, "fallback")
        self.assertEqual(result.decision.mission_action, MissionAction.SUCCESS)
