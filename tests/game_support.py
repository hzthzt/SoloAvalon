from __future__ import annotations

import json
import re
import time
from backend.app.ai.player import AiPlayer
from backend.app.llm.provider import LlmCompletionResult
from backend.app.services.game_service import GameService
from backend.app.services.game_work import GameUnitOfWork


def make_service(database=":memory:", provider=None, **options):
    return GameService(database, ai_player=AiPlayer(provider=provider or _DeterministicProvider()), **options)


def drain(service, game_id, limit=200):
    for _ in range(limit):
        if service.runner.advance_one(game_id).status != "advanced":
            return service.queries.get_game(game_id)
    raise AssertionError("test drain exceeded limit")


def play_to_completion(service, game_id):
    for _ in range(120):
        game = drain(service, game_id)
        if game["phase"] == "complete":
            return game
        if game["status"] == "error_paused":
            raise AssertionError(service.runner.diagnostics)
        service.application.request_human_ai(game_id, game["revision"])
    raise AssertionError("game did not complete")


def wait_until(call, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = call()
        if predicate(result):
            return result
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class _DeterministicProvider:
    def chat_completion(self, profile, messages):
        return LlmCompletionResult(content=self._content(profile, messages))

    def _content(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        action_text = _current_action_text(text)
        if "请求你执行 propose_team" in action_text:
            team_size = _team_size(action_text)
            return json.dumps(
                {
                    "team": _player_ids(action_text)[:team_size],
                    "private_reason_summary": "测试模型选择合法车队。",
                    "public_message": "我先给一个方便观察票型的车队。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 speak" in action_text:
            return json.dumps(
                {
                    "public_message": "我先围绕当前车队观察大家的表态，投票分布会很有价值。",
                    "private_reason_summary": "测试模型输出发言。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 vote" in action_text:
            return json.dumps(
                {
                    "vote": "approve",
                    "private_reason_summary": "测试模型投赞成票。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 mission_action" in action_text:
            return json.dumps(
                {
                    "mission_action": "success",
                    "private_reason_summary": "测试模型提交合法任务行动。",
                },
                ensure_ascii=False,
            )
        if "请求你执行 assassinate" in action_text or "请求你执行 use_lady_of_lake" in action_text:
            viewer = _viewer_id(text)
            candidates = [player_id for player_id in _player_ids(action_text) if player_id != viewer]
            return json.dumps(
                {
                    "target_player_id": candidates[0],
                    "private_reason_summary": "测试模型选择合法刺杀目标。",
                    "candidate_ranking": candidates,
                },
                ensure_ascii=False,
            )
        raise RuntimeError("unhandled test prompt")


class _TimeoutTeamProposalProvider(_DeterministicProvider):
    def chat_completion(self, profile, messages):
        text = "\n".join(message["content"] for message in messages)
        action_text = _current_action_text(text)
        if "请求你执行 propose_team" in action_text:
            raise TimeoutError("测试组队超时")
        return super().chat_completion(profile, messages)


def _current_action_text(text):
    return text.split("【本次行动】", 1)[-1]


def _team_size(text):
    match = re.search(r"车队人数：(\d+)", text)
    return int(match.group(1)) if match else 2


def _player_ids(text):
    player_ids = []
    for player_id in re.findall(r"player_\d+", text):
        if player_id not in player_ids:
            player_ids.append(player_id)
    return player_ids


def _viewer_id(text):
    match = re.search(r"你是 (player_\d+)", text)
    return match.group(1) if match else ""
