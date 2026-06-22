import tempfile
import unittest
from pathlib import Path

from backend.app.game.models import Phase
from backend.app.game.rules import create_five_player_game
from backend.app.storage.ai_decision_repository import (
    AiDecisionInput,
    AiDecisionRepository,
)
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.game_repository import GameRepository


class AiDecisionRepositoryTests(unittest.TestCase):
    def test_save_and_list_ai_decisions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=20260615),
                    game_id="game_1",
                )
                repository = AiDecisionRepository(connection)

                saved = repository.save_decision(
                    AiDecisionInput(
                        game_id="game_1",
                        player_id="player_2",
                        phase=Phase.SPEECH.value,
                        decision_type="speech",
                        input_summary="phase=speech;round=1;viewer=player_2;events=0",
                        strategy_summary="model speech",
                        output={"public_message": "I support this team."},
                        model_name="test-model",
                        llm_profile_id=None,
                        prompt_template_name="speech",
                        prompt_template_version="prompt.v1",
                        context_builder_version="context-builder.v1",
                        stable_prefix_hash="hash",
                        cache_strategy="stable-prefix-v1",
                        context_summary="summary",
                        context_truncated=False,
                        output_raw=None,
                        output_parsed=None,
                        validation_status="valid",
                        prompt_tokens=100,
                        completion_tokens=25,
                        total_tokens=125,
                        cached_tokens=40,
                        cache_hit_rate=0.4,
                    )
                )

                decisions = repository.list_decisions("game_1")

                self.assertEqual(saved.id, 1)
                self.assertEqual(len(decisions), 1)
                self.assertEqual(decisions[0].decision_type, "speech")
                self.assertEqual(decisions[0].output["public_message"], "I support this team.")
                self.assertFalse(decisions[0].context_truncated)
                self.assertEqual(decisions[0].prompt_tokens, 100)
                self.assertEqual(decisions[0].completion_tokens, 25)
                self.assertEqual(decisions[0].total_tokens, 125)
                self.assertEqual(decisions[0].cached_tokens, 40)
                self.assertEqual(decisions[0].cache_hit_rate, 0.4)
            finally:
                connection.close()

    def test_save_decision_can_defer_commit_to_outer_transaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connection = connect_sqlite(Path(tmpdir) / "soloavalon.sqlite3")
            try:
                initialize_database(connection)
                GameRepository(connection).save_new_game(
                    create_five_player_game(seed=20260616),
                    game_id="game_1",
                )
                repository = AiDecisionRepository(connection, autocommit=False)

                repository.save_decision(
                    AiDecisionInput(
                        game_id="game_1",
                        player_id="player_2",
                        phase=Phase.SPEECH.value,
                        decision_type="speech",
                        input_summary="phase=speech;round=1;viewer=player_2;events=0",
                        strategy_summary="model speech",
                        output={"public_message": "I support this team."},
                        model_name="test-model",
                        llm_profile_id=None,
                        prompt_template_name="speech",
                        prompt_template_version="prompt.v1",
                        context_builder_version="context-builder.v1",
                        stable_prefix_hash="hash",
                        cache_strategy="stable-prefix-v1",
                        context_summary="summary",
                        context_truncated=False,
                        output_raw=None,
                        output_parsed=None,
                        validation_status="valid",
                    )
                )
                connection.rollback()

                self.assertEqual(repository.list_decisions("game_1"), [])
            finally:
                connection.close()
