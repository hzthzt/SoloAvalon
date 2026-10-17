import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.game.rules import create_five_player_game
from backend.app.llm.profiles import LlmProfileInput
from backend.app.services.game_service import GameService
from backend.app.storage.database import connect_sqlite, initialize_database
from backend.app.storage.game_repository import GameRepository
from backend.app.storage.llm_profile_repository import LlmProfileRepository


class LlmProfileSelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config_path = Path(temp.name) / "llm_profiles.json"
        connection = connect_sqlite(":memory:")
        self.addCleanup(connection.close)
        initialize_database(connection)
        self.games = GameRepository(connection)
        self.profiles = LlmProfileRepository(config_path=self.config_path)
        with patch.dict("os.environ", {"SOLOAVALON_LLM_CONFIG": str(self.config_path)}):
            self.service = GameService(connection)
        for profile_id in ("default", "override"):
            self.profiles.create_profile(
                profile_id,
                LlmProfileInput(
                    name=profile_id,
                    base_url="https://api.example.com/v1",
                    api_key="test-key",
                    model=profile_id,
                    timeout=30,
                ),
            )

    def test_player_override_takes_precedence_over_game_default(self):
        self.games.save_new_game(
            create_five_player_game(seed=41), "game_1", default_llm_profile_id="default"
        )
        self.games.set_player_llm_profile("game_1", "player_3", "override")

        self.assertEqual(self.service._profile_for_ai("game_1", "player_2").id, "default")
        self.assertEqual(self.service._profile_for_ai("game_1", "player_3").id, "override")

    def test_missing_configuration_keeps_unconfigured_placeholder(self):
        self.games.save_new_game(create_five_player_game(seed=42), "game_1")

        self.assertEqual(self.service._profile_for_ai("game_1", "player_2").id, "unconfigured")

    def test_missing_override_does_not_silently_use_default(self):
        self.games.save_new_game(
            create_five_player_game(seed=43), "game_1", default_llm_profile_id="default"
        )
        self.games.set_player_llm_profile("game_1", "player_3", "missing")

        self.assertEqual(self.service._profile_for_ai("game_1", "player_3").id, "unconfigured")
