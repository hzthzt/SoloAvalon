import assert from "node:assert/strict";
import test from "node:test";

import type { GameEvent } from "./api.js";
import { buildFlowReview, eventsForFlowReview, feedItemsForDisplay } from "./flowReview.js";

const playerNames = new Map([
  ["player_1", "You"],
  ["player_2", "AI 1"],
  ["player_3", "AI 2"],
  ["player_4", "AI 3"],
  ["player_5", "AI 4"]
]);

test("keeps team and speech public while vote is unresolved", () => {
  const events: GameEvent[] = [
    event(1, "team_proposed", {
      leader_player_id: "player_1",
      team: ["player_1", "player_2"]
    }),
    event(2, "speech", {
      player_id: "player_1",
      message: "我支持这个车队"
    }),
    event(3, "vote_cast", {
      player_id: "player_1"
    }),
    event(4, "vote_cast", {
      player_id: "player_2"
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.deepEqual(review.rounds[0].team?.members, ["You", "AI 1"]);
  assert.equal(review.rounds[0].speeches[0].message, "我支持这个车队");
  assert.equal(review.rounds[0].vote, undefined);
  assert.equal(review.broadcasts.some((row) => row.text.includes("提交车队")), true);
  assert.equal(review.broadcasts.some((row) => row.text.includes("赞成")), false);
});

test("reveals the full vote slate only after vote result", () => {
  const events: GameEvent[] = [
    event(1, "team_proposed", {
      leader_player_id: "player_1",
      team: ["player_1", "player_2"]
    }),
    event(2, "vote_cast", {
      player_id: "player_1",
      vote: "approve"
    }),
    event(3, "vote_cast", {
      player_id: "player_2",
      vote: "reject"
    }),
    event(4, "vote_cast", {
      player_id: "player_3",
      vote: "approve"
    }),
    event(5, "vote_cast", {
      player_id: "player_4",
      vote: "reject"
    }),
    event(6, "vote_cast", {
      player_id: "player_5",
      vote: "approve"
    }),
    event(7, "vote_result", {
      approved: true,
      failed_team_votes: 0
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.deepEqual(review.rounds[0].vote?.approvals, ["You", "AI 2", "AI 4"]);
  assert.deepEqual(review.rounds[0].vote?.rejections, ["AI 1", "AI 3"]);
  assert.equal(review.rounds[0].vote?.approved, true);
  assert.equal(review.broadcasts.some((row) => row.text.includes("投票公开")), true);
  assert.equal(review.broadcasts.some((row) => row.kind === "vote_cast"), false);
  assert.equal(review.broadcasts.some((row) => row.text.includes("已投票")), false);
});

test("broadcasts quest result without per-player quest actions", () => {
  const events: GameEvent[] = [
    event(1, "quest_action_submitted", {
      player_id: "player_1"
    }),
    event(2, "quest_action_submitted", {
      player_id: "player_2"
    }),
    event(3, "quest_result", {
      quest_results: ["success"],
      phase: "assassination",
      winner: null
    }),
    event(4, "assassination", {
      assassin_player_id: "player_2",
      target_player_id: "player_1",
      winner: "evil"
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.equal(review.rounds[0].questResult?.result, "success");
  assert.equal(review.rounds[0].assassination?.target, "You");
  assert.equal(review.broadcasts.some((row) => row.text.includes("任务成功")), true);
  assert.equal(review.broadcasts.some((row) => row.kind === "quest_action_submitted"), false);
  assert.equal(review.broadcasts.some((row) => row.text.includes("已提交任务行动")), false);
  assert.equal(review.broadcasts.some((row) => row.text.includes("刺杀")), true);
});

test("includes detailed quest action votes when private log data is available", () => {
  const events: GameEvent[] = [
    event(
      1,
      "quest_action_submitted",
      {
        player_id: "player_1"
      },
      {
        mission_action: "success"
      }
    ),
    event(
      2,
      "quest_action_submitted",
      {
        player_id: "player_2"
      },
      {
        mission_action: "fail"
      }
    ),
    event(3, "quest_result", {
      quest_results: ["fail"],
      phase: "team_proposal",
      winner: null
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.deepEqual(review.rounds[0].questActions, [
    {
      eventIndex: 1,
      player: "You",
      action: "success"
    },
    {
      eventIndex: 2,
      player: "AI 1",
      action: "fail"
    }
  ]);
});

test("does not expose quest action detail from public events", () => {
  const events: GameEvent[] = [
    event(1, "quest_action_submitted", {
      player_id: "player_1"
    }),
    event(2, "quest_result", {
      quest_results: ["success"],
      phase: "team_proposal",
      winner: null
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.deepEqual(review.rounds[0].questActions, []);
});

test("keeps all broadcast rows in the unified information feed", () => {
  const events = Array.from({ length: 30 }, (_, index) =>
    event(index + 1, "speech", {
      player_id: `player_${(index % 5) + 1}`,
      message: `第 ${index + 1} 条广播`
    })
  );

  const review = buildFlowReview(events, playerNames);
  const broadcastItems = review.feedItems.filter((item) => item.type === "broadcast");

  assert.equal(broadcastItems.length, 30);
  assert.equal(broadcastItems[0].eventIndex, 1);
  assert.equal(broadcastItems[29].eventIndex, 30);
});

test("combines round summaries and broadcasts in one feed model", () => {
  const events: GameEvent[] = [
    event(1, "team_proposed", {
      leader_player_id: "player_1",
      team: ["player_1", "player_2"]
    }),
    event(2, "speech", {
      player_id: "player_2",
      message: "我会观察票型"
    })
  ];

  const review = buildFlowReview(events, playerNames);

  assert.equal(review.feedItems.some((item) => item.type === "round"), true);
  assert.equal(review.feedItems.some((item) => item.type === "broadcast"), true);
});

test("displays information feed from oldest to newest", () => {
  const review = buildFlowReview(
    [
      event(1, "speech", {
        player_id: "player_1",
        message: "第一条"
      }),
      event(2, "speech", {
        player_id: "player_2",
        message: "第二条"
      }),
      event(3, "speech", {
        player_id: "player_3",
        message: "第三条"
      })
    ],
    playerNames
  );

  const displayedItems = feedItemsForDisplay(review);
  const eventIndexes = displayedItems.map((item) => item.eventIndex);

  assert.deepEqual(eventIndexes, [...eventIndexes].sort((first, second) => first - second));
  assert.equal(eventIndexes[0], 1);
  assert.equal(eventIndexes[eventIndexes.length - 1], 3);
});

test("uses fetched events when game state events are missing or stale", () => {
  const fetchedEvents = [
    event(1, "speech", {
      player_id: "player_1",
      message: "完整事件日志里的发言"
    }),
    event(2, "speech", {
      player_id: "player_2",
      message: "完整事件日志里的第二条发言"
    })
  ];
  const newerStateEvents = [
    ...fetchedEvents,
    event(3, "speech", {
      player_id: "player_3",
      message: "状态里更新的一条发言"
    })
  ];

  assert.deepEqual(eventsForFlowReview(undefined, fetchedEvents), fetchedEvents);
  assert.deepEqual(eventsForFlowReview([], fetchedEvents), fetchedEvents);
  assert.deepEqual(eventsForFlowReview([fetchedEvents[0]], fetchedEvents), fetchedEvents);
  assert.deepEqual(eventsForFlowReview(newerStateEvents, fetchedEvents), newerStateEvents);
});

function event(
  eventIndex: number,
  eventType: string,
  publicPayload: Record<string, unknown>,
  privatePayload?: Record<string, unknown>
): GameEvent {
  return {
    id: eventIndex,
    game_id: "game_1",
    event_index: eventIndex,
    event_type: eventType,
    public_payload: publicPayload,
    private_payload: privatePayload,
    created_at: `2026-06-16T00:00:0${eventIndex}Z`
  };
}
