from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from backend.app.ai.player import AiDecisionError
from backend.app.errors import describe_error, public_error
from backend.app.game.models import Faction, Phase, Role
from .event_visibility import personal_reason_records, public_event_dicts
from .game_flow import GameReplayError, GameStepRunner
from .game_work import GameUnitOfWork, advance_mode, human_player, next_human_action

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepOutcome:
    status: str


class GameAdvanceError(RuntimeError):
    def __init__(self, snapshot, cause):
        super().__init__(str(cause))
        self.snapshot = snapshot


def _actor(state, mode):
    human_action = next_human_action(state)
    if mode == "human_once" and human_action is not None:
        return human_player(state).id, human_action
    if human_action is not None or state.phase == Phase.COMPLETE:
        return None, None
    if state.phase == Phase.TEAM_PROPOSAL:
        return state.players[state.leader_index].id, "propose_team"
    if state.phase == Phase.SPEECH:
        return state.speech_order[len(state.speeches)], "speak"
    if state.phase == Phase.VOTING:
        player = next((player for player in state.players if player.id not in state.votes), None)
        return (player.id, "vote") if player else (None, "finalize_vote")
    if state.phase == Phase.QUEST:
        player_id = next((pid for pid in state.proposed_team if pid not in state.quest_actions), None)
        return (player_id, "mission_action") if player_id else (None, "finalize_quest")
    if state.phase == Phase.ASSASSINATION:
        return next(player.id for player in state.players if player.role == Role.ASSASSIN), "assassinate"
    if state.phase == Phase.LADY_OF_LAKE:
        return state.lady_of_lake_holder_player_id, "use_lady_of_lake"
    raise ValueError(f"unsupported phase: {state.phase}")


def _executable(summary):
    return summary.status == "active" and summary.archived_at is None and summary.advance_mode is not None


def _same_execution(current, snapshot):
    return (
        current is not None and _executable(current)
        and (current.revision, current.advance_mode, current.created_at)
        == (snapshot.revision, snapshot.advance_mode, snapshot.created_at)
    )


class GameRunner:
    def __init__(self, database, decisions, notifier, *, max_workers=4, scan_seconds=1.0, max_steps=100):
        self.database = database
        self.decisions = decisions
        self.notifier = notifier
        self.max_workers = max_workers
        self.scan_seconds = scan_seconds
        self.max_steps = max_steps
        self._guard = threading.Lock()
        self._running = set()
        self._stepping = set()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._executor = None
        self._scheduler = None
        self.diagnostics = {}

    def wake(self):
        self._wake.set()

    def recover(self):
        with GameUnitOfWork(self.database) as work:
            ids = [summary.id for summary in work.games.list_games()]
        for game_id in ids:
            changed = False
            with GameUnitOfWork(self.database, write=True) as work:
                summary = work.summary(game_id)
                try:
                    state, _ = work.load(game_id)
                except (GameReplayError, ValueError, KeyError, TypeError) as error:
                    self.diagnostics[game_id] = str(error)
                    logger.error("Cannot recover %s: %s", game_id, error)
                    if summary.status == "active" and summary.archived_at is None:
                        work.games.update_runtime(game_id, advance_mode=summary.advance_mode, status="error_paused")
                        changed = True
                else:
                    work.games.rebuild_rule_summary(game_id, state)
                    if summary.status == "active" and summary.archived_at is None:
                        mode = summary.advance_mode or advance_mode(state)
                        if state.phase == Phase.COMPLETE:
                            mode = None
                        if mode != summary.advance_mode:
                            work.games.update_runtime(game_id, advance_mode=mode)
                            changed = True
            if changed:
                self.notifier.notify(game_id)

    def start(self):
        if self._scheduler is not None:
            return
        self._stopping.clear()
        self.recover()
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="game")
        self._scheduler = threading.Thread(target=self._schedule, name="game-scanner", daemon=True)
        self._scheduler.start()

    def stop(self):
        self._stopping.set()
        self.wake()
        if self._scheduler is not None:
            self._scheduler.join()
            self._scheduler = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _schedule(self):
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                with GameUnitOfWork(self.database) as work:
                    pending = [game.id for game in work.games.list_games() if _executable(game)]
                for game_id in pending:
                    with self._guard:
                        if self._stopping.is_set() or len(self._running) >= self.max_workers:
                            break
                        if game_id in self._running:
                            continue
                        self._running.add(game_id)
                    self._executor.submit(self._run, game_id)
            except Exception:
                logger.exception("Game scan failed")
            self._wake.wait(self.scan_seconds)

    def _run(self, game_id):
        try:
            for _ in range(self.max_steps):
                if self._stopping.is_set():
                    return
                if self.advance_one(game_id).status != "advanced":
                    return
            self._pause_limit(game_id)
        except Exception as error:
            if self._stopping.is_set():
                return
            self.diagnostics[game_id] = str(error)
            logger.exception("Game runner failed for %s", game_id)
            try:
                with GameUnitOfWork(self.database, write=True) as work:
                    summary = work.games.get_game_summary(game_id)
                    if isinstance(error, GameAdvanceError) and _same_execution(summary, error.snapshot):
                        work.events.append_event(game_id, "runner_error", describe_error(error))
                        work.games.update_runtime(game_id, advance_mode=summary.advance_mode, status="error_paused")
                self.notifier.notify(game_id)
            except Exception:
                logger.exception("Cannot persist runner pause for %s", game_id)
        finally:
            with self._guard:
                self._running.discard(game_id)
            self.wake()

    def _pause_limit(self, game_id):
        changed = False
        with GameUnitOfWork(self.database, write=True) as work:
            summary = work.summary(game_id)
            if _executable(summary):
                work.events.append_event(game_id, "runner_error", public_error("advance_limit"))
                work.games.update_runtime(game_id, advance_mode=summary.advance_mode, status="error_paused")
                changed = True
        if changed:
            self.notifier.notify(game_id)

    def advance_one(self, game_id):
        with self._guard:
            if game_id in self._stepping:
                return StepOutcome("busy")
            self._stepping.add(game_id)
        try:
            return self._advance_one(game_id)
        finally:
            with self._guard:
                self._stepping.remove(game_id)

    def _advance_one(self, game_id):
        with GameUnitOfWork(self.database) as work:
            snapshot = work.summary(game_id)
            if not _executable(snapshot):
                return StepOutcome("blocked")
            try:
                state, _ = work.load(game_id)
                player_id, action = _actor(state, snapshot.advance_mode)
                history = work.events.list_events(game_id)
                events = public_event_dicts(history)
                personal_records = personal_reason_records(history, player_id)
            except Exception as error:
                raise GameAdvanceError(snapshot, error) from error
        try:
            return self._execute(
                game_id, state, snapshot, player_id, action, events, personal_records
            )
        except Exception as error:
            raise GameAdvanceError(snapshot, error) from error

    def _execute(self, game_id, state, snapshot, player_id, action, events, personal_records):
        # No connection, transaction or global lock survives the model call.
        profile = None
        failure = None
        try:
            steps = GameStepRunner()
            if action is None:
                step = None
            elif action in {"finalize_vote", "finalize_quest"}:
                step = getattr(steps, action)(state)
            elif action == "mission_action" and next(p for p in state.players if p.id == player_id).faction == Faction.GOOD:
                step = steps.apply_player_action(state, player_id, action, {"mission_action": "success"})
            else:
                profile = self.decisions.profile_for(state, snapshot, player_id)
                step = self.decisions.decide(
                    game_id, state, player_id, action, profile, events, personal_records
                )
        except Exception as error:
            failure = error

        if self._stopping.is_set():
            return StepOutcome("stopped")
        with GameUnitOfWork(self.database, write=True) as work:
            current = work.games.get_game_summary(game_id)
            if not _same_execution(current, snapshot):
                return StepOutcome("discarded")
            latest, _ = work.load(game_id)
            if latest.phase != state.phase or _actor(latest, current.advance_mode) != (player_id, action):
                return StepOutcome("discarded")
            if failure is not None:
                logger.error("Game advance failed for %s", game_id,
                             exc_info=(type(failure), failure, failure.__traceback__))
                if isinstance(failure, AiDecisionError) and profile is not None:
                    decision, audit = self.decisions.error_audit(game_id, state, player_id, action, profile, failure)
                    work.committer.commit_ai_error(game_id, decision, audit)
                detail = describe_error(failure, ai=isinstance(failure, AiDecisionError))
                if profile is not None and not all((profile.base_url, profile.api_key, profile.model)):
                    detail = public_error("llm_configuration")
                work.events.append_event(game_id, "runner_error", detail)
                work.games.update_runtime(game_id, advance_mode=current.advance_mode, status="error_paused")
                self.diagnostics[game_id] = str(failure)
                outcome = "paused"
            elif step is None:
                work.games.update_runtime(game_id, advance_mode=advance_mode(latest))
                outcome = "blocked"
            else:
                work.save_step(game_id, latest, step)
                outcome = "advanced"
        self.notifier.notify(game_id)
        return StepOutcome(outcome)
