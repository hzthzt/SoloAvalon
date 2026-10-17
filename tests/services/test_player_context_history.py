from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from backend.app.services.event_visibility import personal_reason_records, public_event_dicts
from backend.app.services.game_work import GameUnitOfWork
from backend.app.storage.event_store import EventRecord
from tests.game_support import _DeterministicProvider, drain, make_service


class PlayerContextHistoryTests(unittest.TestCase):
    def event(self, index, kind, payload):
        return EventRecord(
            id=index, game_id="game", event_index=index, event_type=kind,
            public_payload=payload, private_payload={"secret": "raw private data"},
            created_at="2026-10-18T00:00:00Z",
        )

    def test_projection_filters_audits_and_anchors_to_public_numbering(self):
        history = [
            self.event(1, "game_created", {}),
            self.event(2, "roles_assigned", {"player_count": 5}),
            self.event(3, "ai_decision", {
                "player_id": "player_2", "validation_status": "valid",
                "decision_type": "speech", "strategy_summary": "my judgment",
                "output_raw": "must not be copied",
            }),
            self.event(4, "speech", {"player_id": "player_2", "message": "public speech"}),
        ]
        base = history[2].public_payload
        for index, update in enumerate((
            {"player_id": "player_3", "strategy_summary": "other player's judgment"},
            {"validation_status": "error", "strategy_summary": "failed judgment"},
            {"strategy_summary": "模型未提供私有理由摘要。"},
            {"strategy_summary": "模型直接输出中文发言。"},
            {"strategy_summary": "模型直接输出中文投票。"},
            {"strategy_summary": "   "},
        ), start=5):
            history.append(self.event(index, "ai_decision", base | update))
        public_before = public_event_dicts(history[:4])
        self.assertEqual(public_before, public_event_dicts(history))
        records = personal_reason_records(history, "player_2")
        self.assertEqual(records, [{
            "player_id": "player_2", "decision_type": "speech",
            "reason_summary": "my judgment", "after_event_index": 2,
        }])
        self.assertNotIn("my judgment", json.dumps(public_event_dicts(history)))
        self.assertNotIn("raw private data", json.dumps(records))
        self.assertNotIn("must not be copied", json.dumps(records))
        self.assertEqual(public_before[-1]["event_index"], 3)

    def test_new_records_preserve_existing_anchors_and_summary_bytes(self):
        history = [
            self.event(1, "game_created", {}),
            self.event(2, "ai_decision", {
                "player_id": "player_2", "validation_status": "valid",
                "decision_type": "vote", "strategy_summary": "  earlier judgment\nwith evidence  ",
            }),
        ]
        before = personal_reason_records(history, "player_2")
        history.extend([
            self.event(3, "vote_cast", {"player_id": "player_2", "vote": "reject"}),
            replace(history[1], id=4, event_index=4,
                    public_payload=history[1].public_payload | {"strategy_summary": "revised judgment"}),
        ])
        after = personal_reason_records(history, "player_2")
        self.assertEqual(before, after[:1])
        self.assertEqual(after[-1]["after_event_index"], 2)
        self.assertEqual(after[0]["reason_summary"], "  earlier judgment\nwith evidence  ")

    def test_committed_personal_reason_reaches_only_owners_next_turn_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.sqlite"
            provider = _RecordingProvider()
            service = make_service(path, provider)
            try:
                game = service.application.create_game(seed=2)
                service.application.request_human_ai(game["id"], game["revision"])
                game = drain(service, game["id"])
                first_reason = provider.reasons[0]
                with GameUnitOfWork(service.database) as work:
                    history_before = work.events.list_events(game["id"])
                    records_before = personal_reason_records(history_before, game["human_player_id"])
            finally:
                service.runner.stop()
                service.database.close()
            provider = _RecordingProvider()
            service = make_service(path, provider)
            try:
                service.application.request_human_ai(game["id"], game["revision"])
                game = drain(service, game["id"])
                own_prompt = "\n".join(m["content"] for m in provider.messages[0])
                self.assertIn(first_reason, own_prompt)
                self.assertIn("【个人判断记录·team_proposal】", own_prompt)
                self.assertGreater(len(provider.messages), 1)
                for messages in provider.messages[1:]:
                    self.assertNotIn(first_reason, "\n".join(m["content"] for m in messages))
                self.assertNotIn(first_reason, json.dumps(service.queries.get_public_events(game["id"])))
                with GameUnitOfWork(service.database) as work:
                    history_after = work.events.list_events(game["id"])
                self.assertEqual(
                    personal_reason_records(history_after, game["human_player_id"])[:len(records_before)],
                    records_before,
                )
            finally:
                service.runner.stop()
                service.database.close()


class _RecordingProvider(_DeterministicProvider):
    def __init__(self):
        self.messages = []
        self.reasons = []

    def _content(self, profile, messages):
        output = json.loads(super()._content(profile, messages))
        reason = f"personal-history-marker-{id(self)}-{len(self.messages)}"
        output["private_reason_summary"] = reason
        self.messages.append(messages)
        self.reasons.append(reason)
        return json.dumps(output, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
