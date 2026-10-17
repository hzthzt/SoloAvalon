from __future__ import annotations

from backend.app.ai.player import AiPlayer
from backend.app.storage.database import Database
from backend.app.storage.llm_profile_repository import LlmProfileRepository
from .ai_decision_runner import AiDecisionRunner
from .event_notifier import GameEventNotifier
from .game_application import GameApplication
from .game_queries import GameQueries
from .game_runner import GameRunner


class GameService:
    """Per-application assembly; commands, queries and background work have distinct owners."""

    def __init__(self, database, ai_player=None, event_notifier=None, profiles=None, **runner_options):
        self.database = database if isinstance(database, Database) else Database(database)
        self.event_notifier = event_notifier or GameEventNotifier()
        self.profiles = profiles or LlmProfileRepository()
        self.decisions = AiDecisionRunner(ai_player or AiPlayer(), self.profiles)
        self.application = GameApplication(self.database, self.event_notifier)
        self.queries = GameQueries(self.database)
        self.runner = GameRunner(self.database, self.decisions, self.event_notifier, **runner_options)
        self.application.wake = self.runner.wake
