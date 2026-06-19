import type { GameEvent } from "./api.js";

export type FlowReview = {
  rounds: ReviewRound[];
  broadcasts: BroadcastRow[];
  feedItems: InformationFeedItem[];
};

export type InformationFeedItem = RoundFeedItem | BroadcastFeedItem;

export type RoundFeedItem = {
  type: "round";
  id: string;
  eventIndex: number;
  roundNumber: number;
  round: ReviewRound;
};

export type BroadcastFeedItem = {
  type: "broadcast";
  id: string;
  eventIndex: number;
  roundNumber: number;
  broadcast: BroadcastRow;
};

export type ReviewRound = {
  roundNumber: number;
  team?: TeamProposalReview;
  proposals: TeamProposalReview[];
  speeches: SpeechReview[];
  vote?: VoteReview;
  votes: VoteReview[];
  questActions: QuestActionReview[];
  questResult?: QuestResultReview;
  assassination?: AssassinationReview;
};

export type TeamProposalReview = {
  eventIndex: number;
  leader: string;
  members: string[];
};

export type SpeechReview = {
  eventIndex: number;
  player: string;
  message: string;
};

export type VoteReview = {
  eventIndex: number;
  approved: boolean;
  approvals: string[];
  rejections: string[];
  failedTeamVotes: number;
};

export type QuestResultReview = {
  eventIndex: number;
  result: "success" | "fail";
  results: Array<"success" | "fail">;
  successCount: number;
  failCount: number;
  winner: string | null;
};

export type QuestActionReview = {
  eventIndex: number;
  player: string;
  action: "success" | "fail";
};

export type AssassinationReview = {
  eventIndex: number;
  assassin: string;
  target: string;
  winner: string | null;
};

export type BroadcastRow = {
  id: string;
  eventIndex: number;
  roundNumber: number;
  kind: string;
  text: string;
  createdAt: string;
};

type PendingVote = {
  playerId: string;
  vote?: string;
};

export function eventsForFlowReview(
  stateEvents: GameEvent[] | undefined,
  fetchedEvents: GameEvent[]
): GameEvent[] {
  if (!stateEvents?.length) {
    return fetchedEvents;
  }
  return latestEventIndex(fetchedEvents) > latestEventIndex(stateEvents) ? fetchedEvents : stateEvents;
}

export function feedItemsForDisplay(review: FlowReview): InformationFeedItem[] {
  return [...review.feedItems];
}

export function buildFlowReview(
  events: GameEvent[],
  playerNames: ReadonlyMap<string, string>
): FlowReview {
  const rounds: ReviewRound[] = [];
  const broadcasts: BroadcastRow[] = [];
  let currentRoundNumber = 1;
  let pendingVotes: PendingVote[] = [];

  for (const event of [...events].sort((first, second) => first.event_index - second.event_index)) {
    const round = ensureRound(rounds, currentRoundNumber);
    const payload = event.public_payload;

    if (event.event_type === "game_created") {
      broadcasts.push(broadcast(event, currentRoundNumber, "system", "对局创建"));
      continue;
    }

    if (event.event_type === "roles_assigned") {
      broadcasts.push(broadcast(event, currentRoundNumber, "system", "身份已分配"));
      continue;
    }

    if (event.event_type === "private_view_recorded" || event.event_type === "ai_decision") {
      continue;
    }

    if (event.event_type === "team_proposed") {
      const proposal = {
        eventIndex: event.event_index,
        leader: nameFor(playerNames, valueAsString(payload.leader_player_id)),
        members: valueAsStringArray(payload.team).map((playerId) => nameFor(playerNames, playerId))
      };
      round.team = proposal;
      round.proposals.push(proposal);
      pendingVotes = [];
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "team_proposed",
          `${proposal.leader} 提交车队：${joinNames(proposal.members)}`
        )
      );
      continue;
    }

    if (event.event_type === "speech") {
      const speech = {
        eventIndex: event.event_index,
        player: nameFor(playerNames, valueAsString(payload.player_id)),
        message: valueAsString(payload.message)
      };
      round.speeches.push(speech);
      broadcasts.push(
        broadcast(event, currentRoundNumber, "speech", `${speech.player} 发言：${speech.message}`)
      );
      continue;
    }

    if (event.event_type === "vote_cast") {
      const playerId = valueAsString(payload.player_id);
      pendingVotes.push({
        playerId,
        vote: valueAsString(payload.vote)
      });
      continue;
    }

    if (event.event_type === "vote_result") {
      const approvals = pendingVotes
        .filter((vote) => vote.vote === "approve")
        .map((vote) => nameFor(playerNames, vote.playerId));
      const rejections = pendingVotes
        .filter((vote) => vote.vote === "reject")
        .map((vote) => nameFor(playerNames, vote.playerId));
      const voteReview = {
        eventIndex: event.event_index,
        approved: valueAsBoolean(payload.approved),
        approvals,
        rejections,
        failedTeamVotes: valueAsNumber(payload.failed_team_votes)
      };
      round.vote = voteReview;
      round.votes.push(voteReview);
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "vote_result",
          `投票公开：${voteSlateText(approvals, rejections)}`
        )
      );
      pendingVotes = [];
      continue;
    }

    if (event.event_type === "quest_action_submitted") {
      const action = missionActionFromPrivatePayload(event.private_payload);
      if (action) {
        round.questActions.push({
          eventIndex: event.event_index,
          player: nameFor(playerNames, valueAsString(payload.player_id)),
          action
        });
      }
      continue;
    }

    if (event.event_type === "quest_result") {
      const results = valueAsStringArray(payload.quest_results).filter(isQuestResult);
      const result = results[results.length - 1] ?? "success";
      const questResult = {
        eventIndex: event.event_index,
        result,
        results,
        successCount: results.filter((item) => item === "success").length,
        failCount: results.filter((item) => item === "fail").length,
        winner: nullableString(payload.winner)
      };
      round.questResult = questResult;
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "quest_result",
          `任务${questResultLabel(result)}，当前任务战绩：${questResult.successCount} 成功 / ${questResult.failCount} 失败`
        )
      );
      pendingVotes = [];
      if (valueAsString(payload.phase) === "team_proposal") {
        currentRoundNumber += 1;
        ensureRound(rounds, currentRoundNumber);
      }
      continue;
    }

    if (event.event_type === "assassination") {
      const assassination = {
        eventIndex: event.event_index,
        assassin: nameFor(playerNames, valueAsString(payload.assassin_player_id)),
        target: nameFor(playerNames, valueAsString(payload.target_player_id)),
        winner: nullableString(payload.winner)
      };
      round.assassination = assassination;
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "assassination",
          `${assassination.assassin} 刺杀 ${assassination.target}，${winnerLabel(assassination.winner)}`
        )
      );
    }

    if (event.event_type === "lady_of_lake_used") {
      const viewer = nameFor(playerNames, valueAsString(payload.viewer_player_id));
      const target = nameFor(playerNames, valueAsString(payload.target_player_id));
      const inspectedRound = valueAsNumber(payload.round_number);
      broadcasts.push(
        broadcast(
          event,
          inspectedRound || currentRoundNumber,
          "lady_of_lake_used",
          `${viewer} 使用湖中仙女查看 ${target}`
        )
      );
      if (inspectedRound >= currentRoundNumber) {
        currentRoundNumber = inspectedRound + 1;
        ensureRound(rounds, currentRoundNumber);
      }
      continue;
    }
  }

  const visibleRounds = rounds.filter((round) => hasRoundContent(round));
  return {
    rounds: visibleRounds,
    broadcasts,
    feedItems: buildFeedItems(visibleRounds, broadcasts)
  };
}

function ensureRound(rounds: ReviewRound[], roundNumber: number): ReviewRound {
  let round = rounds.find((item) => item.roundNumber === roundNumber);
  if (!round) {
    round = {
      roundNumber,
      proposals: [],
      speeches: [],
      votes: [],
      questActions: []
    };
    rounds.push(round);
  }
  return round;
}

function hasRoundContent(round: ReviewRound): boolean {
  return Boolean(
    round.team ||
      round.proposals.length ||
      round.speeches.length ||
      round.vote ||
      round.votes.length ||
      round.questActions.length ||
      round.questResult ||
      round.assassination
  );
}

function buildFeedItems(rounds: ReviewRound[], broadcasts: BroadcastRow[]): InformationFeedItem[] {
  const roundItems = rounds.map((round) => ({
    type: "round" as const,
    id: `round-${round.roundNumber}`,
    eventIndex: latestRoundEventIndex(round),
    roundNumber: round.roundNumber,
    round
  }));
  const broadcastItems = broadcasts.map((row) => ({
    type: "broadcast" as const,
    id: `broadcast-${row.id}`,
    eventIndex: row.eventIndex,
    roundNumber: row.roundNumber,
    broadcast: row
  }));
  return [...roundItems, ...broadcastItems].sort(
    (first, second) => first.eventIndex - second.eventIndex
  );
}

function latestRoundEventIndex(round: ReviewRound): number {
  return Math.max(
    0,
    ...round.proposals.map((proposal) => proposal.eventIndex),
    ...round.speeches.map((speech) => speech.eventIndex),
    ...round.votes.map((vote) => vote.eventIndex),
    ...round.questActions.map((action) => action.eventIndex),
    round.questResult?.eventIndex ?? 0,
    round.assassination?.eventIndex ?? 0
  );
}

function latestEventIndex(events: GameEvent[]): number {
  return Math.max(0, ...events.map((event) => event.event_index));
}

function broadcast(
  event: GameEvent,
  roundNumber: number,
  kind: string,
  text: string
): BroadcastRow {
  return {
    id: `${event.event_index}-${kind}`,
    eventIndex: event.event_index,
    roundNumber,
    kind,
    text,
    createdAt: event.created_at
  };
}

function nameFor(playerNames: ReadonlyMap<string, string>, playerId: string): string {
  return playerNames.get(playerId) ?? playerId;
}

function valueAsString(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function nullableString(value: unknown): string | null {
  const text = valueAsString(value);
  return text || null;
}

function valueAsStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(valueAsString).filter(Boolean) : [];
}

function valueAsBoolean(value: unknown): boolean {
  return value === true || value === "true";
}

function valueAsNumber(value: unknown): number {
  return typeof value === "number" ? value : Number(value) || 0;
}

function isQuestResult(value: string): value is "success" | "fail" {
  return value === "success" || value === "fail";
}

function missionActionFromPrivatePayload(
  privatePayload: Record<string, unknown> | null | undefined
): "success" | "fail" | null {
  const action = valueAsString(privatePayload?.mission_action);
  return isQuestResult(action) ? action : null;
}

function joinNames(names: string[]): string {
  return names.length ? names.join("、") : "无";
}

function voteSlateText(approvals: string[], rejections: string[]): string {
  return `${joinNames(approvals)} 赞成；${joinNames(rejections)} 反对`;
}

function questResultLabel(result: "success" | "fail"): string {
  return result === "success" ? "成功" : "失败";
}

function winnerLabel(winner: string | null): string {
  if (winner === "good") {
    return "好人胜利";
  }
  if (winner === "evil") {
    return "恶方胜利";
  }
  return "胜负未定";
}
