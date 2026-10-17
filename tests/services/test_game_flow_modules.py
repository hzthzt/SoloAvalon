import tempfile
import unittest

from backend.app.ai.player import AiPlayer
from backend.app.game.models import MissionAction, Phase, Vote
from backend.app.services.game_flow import (
    GameCommitter,
    GameStateLoader,
    GameStepRunner,
    PendingEvent,
    StepResult,
)
from backend.app.services.game_service import GameService
from backend.app.storage.ai_decision_repository import AiDecisionInput
from backend.app.storage.ai_memory_repository import AiMemorySnapshotInput
from backend.app.storage.event_store import EventStore
from backend.app.storage.database import connect_sqlite, initialize_database

from tests.game_support import _DeterministicProvider, make_service, drain
from backend.app.storage.game_repository import GameRepository
from backend.app.storage.ai_decision_repository import AiDecisionRepository
from backend.app.storage.ai_memory_repository import AiMemoryRepository
from backend.app.services.game_work import GameUnitOfWork


class GameFlowModuleTests(unittest.TestCase):
    def test_state_loader_replays_rules_and_ignores_audit_events(self):
        service = _service()
        created = service.create_game(seed=2)
        service.submit_human_action(
            created["id"],
            "propose_team",
            {"team": ["player_1", "player_2"]},
        )
        service._events.append_event(
            created["id"],
            "ai_decision",
            {
                "player_id": "player_2",
                "phase": "speech",
                "decision_type": "speech",
                "validation_status": "valid",
                "strategy_summary": "审计事件不改变规则状态。",
            },
            {"prompt_messages": []},
        )

        loaded = GameStateLoader(service._games, service._events).load(created["id"])

        self.assertEqual(loaded.state.phase, Phase.SPEECH)
        self.assertEqual(loaded.last_replayed_event_index, len(service.list_events(created["id"])) - 1)
        self.assertIsNone(loaded.replay_error)

    def test_state_loader_reports_missing_private_payload(self):
        service = _service()
        created = service.create_game(seed=2)
        service._events.append_event(
            created["id"],
            "quest_action_submitted",
            {"player_id": "player_1"},
            None,
        )

        loaded = GameStateLoader(service._games, service._events).load(created["id"])

        self.assertIsNotNone(loaded.replay_error)
        self.assertEqual(str(loaded.replay_error), "cannot restore quest action without private mission_action")
        self.assertEqual(loaded.failed_event_index, len(service.list_events(created["id"])))

    def test_step_runner_human_action_returns_events_without_persisting(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        event_count = len(service.list_events(created["id"]))

        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )

        self.assertEqual(result.state_after.phase, Phase.SPEECH)
        self.assertEqual([event.event_type for event in result.rule_events], ["team_proposed"])
        self.assertEqual(len(service.list_events(created["id"])), event_count)

    def test_committer_persists_step_events_and_summary(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )

        committed = GameCommitter(
            service.connection,
            service._games,
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
        ).commit_step(created["id"], state, result)

        self.assertEqual(committed.phase, Phase.SPEECH)
        self.assertEqual(service.list_events(created["id"])[-1].event_type, "team_proposed")
        self.assertEqual(service.list_games()[0].current_phase, Phase.SPEECH.value)

    def test_committer_notifies_game_after_successful_commit(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )
        notifier = _RecordingNotifier()

        GameCommitter(
            service.connection,
            service._games,
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
            event_notifier=notifier,
        ).commit_step(created["id"], state, result)

        self.assertEqual(notifier.notified_game_ids, [created["id"]])

    def test_committer_rolls_back_rule_events_when_summary_update_fails(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        event_count = len(service.list_events(created["id"]))
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )

        committer = GameCommitter(
            service.connection,
            _FailingGameRepository(service._games),
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
        )

        with self.assertRaises(RuntimeError):
            committer.commit_step(created["id"], state, result)

        self.assertEqual(len(service.list_events(created["id"])), event_count)
        self.assertIs(service._states[created["id"]], state)

    def test_committer_does_not_notify_when_commit_rolls_back(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )
        notifier = _RecordingNotifier()
        committer = GameCommitter(
            service.connection,
            _FailingGameRepository(service._games),
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
            event_notifier=notifier,
        )

        with self.assertRaises(RuntimeError):
            committer.commit_step(created["id"], state, result)

        self.assertEqual(notifier.notified_game_ids, [])

    def test_committer_notifies_game_after_ai_error_commit(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )
        result = _with_ai_audit(created["id"], result)
        notifier = _RecordingNotifier()

        GameCommitter(
            service.connection,
            service._games,
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
            event_notifier=notifier,
        ).commit_ai_error(created["id"], result.ai_decision, result.audit_events[0])

        self.assertEqual(notifier.notified_game_ids, [created["id"]])

    def test_committer_persists_ai_audit_with_rule_step(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )
        result = _with_ai_audit(created["id"], result)

        GameCommitter(
            service.connection,
            service._games,
            service._events,
            service._ai_decisions,
            service._ai_memory,
            service._states,
        ).commit_step(created["id"], state, result)

        self.assertEqual(len(service._ai_decisions.list_decisions(created["id"])), 1)
        self.assertEqual(len(service._ai_memory.list_snapshots(created["id"], "player_2")), 1)
        self.assertEqual(
            [event.event_type for event in service.list_events(created["id"])[-2:]],
            ["ai_decision", "team_proposed"],
        )

    def test_committer_rolls_back_ai_audit_when_rule_event_fails(self):
        service = _service()
        created = service.create_game(seed=2)
        state = service._state(created["id"])
        event_count = len(service.list_events(created["id"]))
        result = GameStepRunner().apply_human_action(
            created["id"],
            state,
            "propose_team",
            {"team": ["player_1", "player_2"]},
            human_player_id="player_1",
        )
        result = _with_ai_audit(created["id"], result)

        committer = GameCommitter(
            service.connection,
            service._games,
            _FailingRuleEventStore(service.connection),
            service._ai_decisions,
            service._ai_memory,
            service._states,
        )

        with self.assertRaises(RuntimeError):
            committer.commit_step(created["id"], state, result)

        self.assertEqual(service._ai_decisions.list_decisions(created["id"]), [])
        self.assertEqual(service._ai_memory.list_snapshots(created["id"], "player_2"), [])
        self.assertEqual(len(service.list_events(created["id"])), event_count)
        self.assertIs(service._states[created["id"]], state)


def _service():
    connection = connect_sqlite(":memory:")
    initialize_database(connection)
    return _FlowFixture(connection)


class _FailingGameRepository:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def update_game_state(self, game_id, state):
        raise RuntimeError("forced summary update failure")

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class _FailingRuleEventStore(EventStore):
    def __init__(self, connection):
        super().__init__(connection, autocommit=False)

    def append_event(self, game_id, event_type, public_payload, private_payload=None):
        if event_type == "team_proposed":
            raise RuntimeError("forced rule event failure")
        return super().append_event(game_id, event_type, public_payload, private_payload)


class _RecordingNotifier:
    def __init__(self):
        self.notified_game_ids = []

    def notify(self, game_id):
        self.notified_game_ids.append(game_id)


def _with_ai_audit(game_id, result):
    return StepResult(
        state_after=result.state_after,
        rule_events=result.rule_events,
        audit_events=(
            PendingEvent(
                "ai_decision",
                {
                    "player_id": "player_2",
                    "phase": Phase.TEAM_PROPOSAL.value,
                    "decision_type": "team_proposal",
                    "validation_status": "valid",
                    "strategy_summary": "测试 AI 审计。",
                },
                {"prompt_messages": []},
            ),
        ),
        ai_decision=AiDecisionInput(
            game_id=game_id,
            player_id="player_2",
            phase=Phase.TEAM_PROPOSAL.value,
            decision_type="team_proposal",
            input_summary="phase=team_proposal",
            strategy_summary="测试 AI 审计。",
            output={"team": ["player_1", "player_2"]},
            model_name="test-model",
            llm_profile_id=None,
            prompt_template_name="team_proposal",
            prompt_template_version="prompt.v1",
            context_builder_version="context-builder.v1",
            stable_prefix_hash="hash",
            cache_strategy="stable-prefix-v1",
            context_summary="summary",
            context_truncated=False,
            output_raw=None,
            output_parsed=None,
            validation_status="valid",
        ),
        ai_memory=AiMemorySnapshotInput(
            game_id=game_id,
            player_id="player_2",
            round_number=1,
            phase=Phase.TEAM_PROPOSAL.value,
            memory_payload={
                "suspicions": {},
                "trusted_players": [],
                "key_observations": ["测试 AI 审计。"],
                "merlin_candidates": [],
            },
        ),
    )


class _FlowFixture:
    def __init__(self, connection):
        self.connection = connection
        self.services = make_service(connection)
        self._games = GameRepository(connection)
        self._events = EventStore(connection)
        self._ai_decisions = AiDecisionRepository(connection)
        self._ai_memory = AiMemoryRepository(connection)
        self._states = {}

    def create_game(self, **kwargs):
        return self.services.application.create_game(**kwargs)

    def _state(self, game_id):
        if game_id not in self._states:
            with GameUnitOfWork(self.services.database) as work:
                self._states[game_id] = work.load(game_id)[0]
        return self._states[game_id]

    def submit_human_action(self, game_id, action, payload):
        revision = self.services.queries.get_game(game_id)["revision"]
        self.services.application.submit_human_action(game_id, revision, action, payload)
        return drain(self.services, game_id)

    def list_events(self, game_id):
        return self._events.list_events(game_id)

    def list_games(self):
        return self._games.list_games()
