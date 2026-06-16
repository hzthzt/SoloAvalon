from __future__ import annotations

from dataclasses import asdict
from typing import Any

from backend.app.storage.event_store import EventRecord


def public_event_dicts(events: list[EventRecord]) -> list[dict[str, Any]]:
    payloads = []
    for event in events:
        payload = asdict(event)
        payload.pop("private_payload", None)
        payloads.append(payload)
    return hide_unsettled_vote_values(payloads)


def hide_unsettled_vote_values(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_events = []
    pending_vote_indexes: list[int] = []
    for event in events:
        public_event = dict(event)
        public_event["public_payload"] = dict(event["public_payload"])
        if public_event["event_type"] == "vote_cast":
            pending_vote_indexes.append(len(public_events))
        elif public_event["event_type"] == "vote_result":
            pending_vote_indexes.clear()
        public_events.append(public_event)

    for index in pending_vote_indexes:
        public_events[index]["public_payload"].pop("vote", None)
    return public_events
