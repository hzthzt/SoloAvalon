import assert from "node:assert/strict";
import test from "node:test";

import type { GameEvent } from "./api.js";
import {
  buildFlowReview,
  currentActivityText,
  eventsForFlowReview,
  feedItemsForDisplay
} from "./flowReview.js";
import type { GameState } from "./api.js";

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
  assert.equal(review.feedItems.some((item) => item.type === "chat"), true);
  assert.equal(review.summaries.length, 0);
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
  assert.equal(review.summaries[0].type, "team_vote_summary");
  assert.equal(review.summaries[0].title, "第 1 轮任务，第 1 次组队");
  assert.deepEqual(review.summaries[0].lines, [
    "队长：You",
    "车队：You、AI 1",
    "赞成：You、AI 2、AI 4",
    "反对：AI 1、AI 3",
    "车队通过"
  ]);
  assert.equal(review.broadcasts.some((row) => row.text.includes("投票结果")), false);
  assert.equal(review.broadcasts.some((row) => row.text.includes("投票结果：")), false);
  assert.equal(
    review.feedItems.some(
      (item) =>
        item.type === "broadcast" &&
        item.broadcast.text.includes("队长：You\n车队：You、AI 1\n赞成：You、AI 2、AI 4\n反对：AI 1、AI 3")
    ),
    true
  );
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
      winner: null,
      success_cards: 2,
      fail_cards: 0
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
  assert.equal(review.summaries.some((summary) => summary.type === "quest_summary"), true);
  assert.deepEqual(review.summaries[0].lines, [
    "任务成功",
    "成功票：2",
    "失败票：0"
  ]);
  assert.equal(review.summaries[0].lines.join("\n").includes("当前任务战绩"), false);
  assert.equal(review.broadcasts.some((row) => row.text.includes("任务成功")), true);
  assert.equal(
    review.broadcasts.some((row) => row.text.includes("任务成功\n成功票：2\n失败票：0")),
    true
  );
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

test("broadcasts lady of lake use without revealing private faction", () => {
  const events: GameEvent[] = [
    event(
      1,
      "lady_of_lake_used",
      {
        viewer_player_id: "player_5",
        target_player_id: "player_2",
        next_holder_player_id: "player_2",
        round_number: 2
      },
      {
        target_faction: "evil"
      }
    )
  ];

  const review = buildFlowReview(events, playerNames);

  assert.equal(review.broadcasts[0].kind, "lady_of_lake_used");
  assert.equal(review.broadcasts[0].text, "AI 4 使用湖中仙女查看 AI 1");
  assert.equal(review.broadcasts[0].text.includes("evil"), false);
  assert.equal(review.broadcasts[0].text.includes("恶方"), false);
});

test("keeps all chat rows in the realtime information feed", () => {
  const events = Array.from({ length: 30 }, (_, index) =>
    event(index + 1, "speech", {
      player_id: `player_${(index % 5) + 1}`,
      message: `第 ${index + 1} 条广播`
    })
  );

  const review = buildFlowReview(events, playerNames);
  const chatItems = review.feedItems.filter((item) => item.type === "chat");

  assert.equal(chatItems.length, 30);
  assert.equal(chatItems[0].eventIndex, 1);
  assert.equal(chatItems[29].eventIndex, 30);
});

test("keeps realtime feed separate from summary cards", () => {
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
  const feedTypes = review.feedItems.map((item) => item.type as string);

  assert.equal(feedTypes.includes("broadcast"), true);
  assert.equal(feedTypes.includes("chat"), true);
  assert.equal(feedTypes.includes("summary"), false);
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

test("increments team attempt number within the same quest round", () => {
  const review = buildFlowReview(
    [
      event(1, "team_proposed", {
        leader_player_id: "player_1",
        team: ["player_1", "player_2"]
      }),
      event(2, "vote_cast", {
        player_id: "player_1",
        vote: "reject"
      }),
      event(3, "vote_cast", {
        player_id: "player_2",
        vote: "reject"
      }),
      event(4, "vote_result", {
        approved: false,
        failed_team_votes: 1
      }),
      event(5, "team_proposed", {
        leader_player_id: "player_2",
        team: ["player_2", "player_3"]
      }),
      event(6, "vote_cast", {
        player_id: "player_1",
        vote: "approve"
      }),
      event(7, "vote_result", {
        approved: true,
        failed_team_votes: 0
      })
    ],
    playerNames
  );

  assert.equal(review.rounds[0].proposals[0].attemptNumber, 1);
  assert.equal(review.rounds[0].proposals[1].attemptNumber, 2);
  assert.deepEqual(
    review.summaries
      .filter((summary) => summary.type === "team_vote_summary")
      .map((summary) => summary.title),
    ["第 1 轮任务，第 1 次组队", "第 1 轮任务，第 2 次组队"]
  );
});

test("current voting activity hides submitted counts", () => {
  assert.equal(
    currentActivityText(
      gameState({
        phase: "voting",
        votes_cast_count: 3,
        failed_team_votes: 1
      }),
      playerNames
    ),
    "第 1 轮任务，第 2 次组队投票中"
  );
  assert.equal(
    currentActivityText(
      gameState({
        phase: "quest",
        quest_actions_submitted_count: 1
      }),
      playerNames
    ),
    "第 1 轮任务，任务投票中"
  );
});

test("current speech activity names the next speaker", () => {
  assert.equal(
    currentActivityText(
      gameState({
        phase: "speech",
        speech_order: ["player_2", "player_3"],
        speeches: {
          player_2: "我先说"
        }
      }),
      playerNames
    ),
    "AI 2发言中"
  );
});

test("current activity keeps accepted team attempt number during quest phase", () => {
  const review = buildFlowReview(
    [
      event(1, "team_proposed", {
        leader_player_id: "player_1",
        team: ["player_1", "player_2"]
      }),
      event(2, "vote_result", {
        approved: false,
        failed_team_votes: 1
      }),
      event(3, "team_proposed", {
        leader_player_id: "player_2",
        team: ["player_2", "player_3"]
      }),
      event(4, "vote_result", {
        approved: true,
        failed_team_votes: 0
      })
    ],
    playerNames
  );

  assert.equal(review.rounds[0].team?.attemptNumber, 2);
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

function gameState(overrides: Partial<GameState>): GameState {
  return {
    id: "game_1",
    display_name: "游戏#1",
    status: "active",
    player_count: 5,
    phase: "team_proposal",
    current_round: 1,
    leader_player_id: "player_1",
    missions: [],
    enabled_options: [],
    human_player_id: "player_1",
    human_role: "merlin",
    human_faction: "good",
    known_evil_player_ids: [],
    lady_of_lake_holder_player_id: null,
    lady_of_lake_previous_holder_ids: [],
    lady_of_lake_eligible_target_ids: [],
    lady_of_lake_known_factions: {},
    players: Array.from(playerNames, ([id, name], index) => ({
      id,
      seat_index: index,
      name,
      original_name: null,
      is_human: id === "player_1",
      visible_role: id === "player_1" ? "merlin" : null,
      revealed_role: "loyal_servant"
    })),
    proposed_team: ["player_1", "player_2"],
    speech_order: [],
    speeches: {},
    votes_cast_count: 0,
    quest_actions_submitted_count: 0,
    quest_results: [],
    failed_team_votes: 0,
    forced_team: false,
    winner: null,
    assassination_target_id: null,
    next_human_action: null,
    ...overrides
  };
}
