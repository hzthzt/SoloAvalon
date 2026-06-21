import type { GameEvent, GameState } from "./api.js";

export type FlowReview = {
  rounds: ReviewRound[];
  broadcasts: BroadcastRow[];
  chatMessages: ChatMessage[];
  summaries: SummaryCard[];
  feedItems: InformationFeedItem[];
};

export type InformationFeedItem = ChatFeedItem | BroadcastFeedItem;

export type ChatFeedItem = {
  type: "chat";
  id: string;
  eventIndex: number;
  roundNumber: number;
  chat: ChatMessage;
};

export type BroadcastFeedItem = {
  type: "broadcast";
  id: string;
  eventIndex: number;
  roundNumber: number;
  broadcast: BroadcastRow;
};

export type SummaryCard = TeamVoteSummary | QuestSummary;

export type TeamVoteSummary = {
  type: "team_vote_summary";
  id: string;
  eventIndex: number;
  roundNumber: number;
  teamAttemptNumber: number;
  title: string;
  lines: string[];
  approved: boolean;
};

export type QuestSummary = {
  type: "quest_summary";
  id: string;
  eventIndex: number;
  roundNumber: number;
  title: string;
  lines: string[];
  result: "success" | "fail";
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
  attemptNumber: number;
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
  attemptNumber: number;
};

export type QuestResultReview = {
  eventIndex: number;
  result: "success" | "fail";
  results: Array<"success" | "fail">;
  successCount: number;
  failCount: number;
  successCards: number | null;
  failCards: number | null;
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

export type ChatMessage = {
  id: string;
  eventIndex: number;
  roundNumber: number;
  player: string;
  message: string;
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

export function mergeEventsForFlowReview(
  currentEvents: GameEvent[],
  incrementalEvents: GameEvent[]
): GameEvent[] {
  const eventsByIndex = new Map<number, GameEvent>();
  for (const event of currentEvents) {
    eventsByIndex.set(event.event_index, event);
  }
  for (const event of incrementalEvents) {
    if (!eventsByIndex.has(event.event_index)) {
      eventsByIndex.set(event.event_index, event);
    }
  }
  return [...eventsByIndex.values()].sort((first, second) => first.event_index - second.event_index);
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
  const chatMessages: ChatMessage[] = [];
  const summaries: SummaryCard[] = [];
  let currentRoundNumber = 1;
  let currentTeamAttemptNumber = 0;
  let pendingTeamAttemptNumber = 0;
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
        members: valueAsStringArray(payload.team).map((playerId) => nameFor(playerNames, playerId)),
        attemptNumber: currentTeamAttemptNumber + 1
      };
      currentTeamAttemptNumber = proposal.attemptNumber;
      pendingTeamAttemptNumber = proposal.attemptNumber;
      round.team = proposal;
      round.proposals.push(proposal);
      pendingVotes = [];
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "team_proposed",
          `第 ${currentRoundNumber} 轮任务，第 ${proposal.attemptNumber} 次组队：${proposal.leader} 提交车队：${joinNames(proposal.members)}`
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
      chatMessages.push({
        id: `${event.event_index}-chat`,
        eventIndex: event.event_index,
        roundNumber: currentRoundNumber,
        player: speech.player,
        message: speech.message,
        createdAt: event.created_at
      });
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
        failedTeamVotes: valueAsNumber(payload.failed_team_votes),
        attemptNumber: pendingTeamAttemptNumber || currentTeamAttemptNumber || 1
      };
      round.vote = voteReview;
      round.votes.push(voteReview);
      const summary = teamVoteSummary(event, currentRoundNumber, voteReview, round.team);
      summaries.push(summary);
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "vote_result",
          [summary.title, ...summary.lines].join("\n")
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
        successCards: valueAsOptionalNumber(payload.success_cards),
        failCards: valueAsOptionalNumber(payload.fail_cards),
        winner: nullableString(payload.winner)
      };
      round.questResult = questResult;
      const summary = questSummary(event, currentRoundNumber, questResult);
      summaries.push(summary);
      broadcasts.push(
        broadcast(
          event,
          currentRoundNumber,
          "quest_result",
          [summary.title, ...summary.lines].join("\n")
        )
      );
      pendingVotes = [];
      if (valueAsString(payload.phase) === "team_proposal") {
        currentRoundNumber += 1;
        currentTeamAttemptNumber = 0;
        pendingTeamAttemptNumber = 0;
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
        currentTeamAttemptNumber = 0;
        pendingTeamAttemptNumber = 0;
        ensureRound(rounds, currentRoundNumber);
      }
      continue;
    }
  }

  const visibleRounds = rounds.filter((round) => hasRoundContent(round));
  return {
    rounds: visibleRounds,
    broadcasts,
    chatMessages,
    summaries,
    feedItems: buildFeedItems(chatMessages, broadcasts)
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

function buildFeedItems(
  chatMessages: ChatMessage[],
  broadcasts: BroadcastRow[]
): InformationFeedItem[] {
  const chatItems = chatMessages.map((chat) => ({
    type: "chat" as const,
    id: `chat-${chat.id}`,
    eventIndex: chat.eventIndex,
    roundNumber: chat.roundNumber,
    chat
  }));
  const broadcastItems = broadcasts.map((row) => ({
    type: "broadcast" as const,
    id: `broadcast-${row.id}`,
    eventIndex: row.eventIndex,
    roundNumber: row.roundNumber,
    broadcast: row
  }));
  return [...chatItems, ...broadcastItems].sort(
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

function teamVoteSummary(
  event: GameEvent,
  roundNumber: number,
  vote: VoteReview,
  team: TeamProposalReview | undefined
): TeamVoteSummary {
  return {
    type: "team_vote_summary",
    id: `${event.event_index}-team-vote-summary`,
    eventIndex: event.event_index,
    roundNumber,
    teamAttemptNumber: vote.attemptNumber,
    title: `第 ${roundNumber} 轮任务，第 ${vote.attemptNumber} 次组队`,
    lines: [
      `队长：${team?.leader ?? "未知"}`,
      `车队：${joinNames(team?.members ?? [])}`,
      `赞成：${joinNames(vote.approvals)}`,
      `反对：${joinNames(vote.rejections)}`,
      vote.approved ? "车队通过" : "车队未通过"
    ],
    approved: vote.approved
  };
}

function questSummary(
  event: GameEvent,
  roundNumber: number,
  questResult: QuestResultReview
): QuestSummary {
  return {
    type: "quest_summary",
    id: `${event.event_index}-quest-summary`,
    eventIndex: event.event_index,
    roundNumber,
    title: `第 ${roundNumber} 轮任务，任务投票结束`,
    lines: [
      `任务${questResultLabel(questResult.result)}`,
      `成功票：${questResult.successCards ?? "未知"}`,
      `失败票：${questResult.failCards ?? "未知"}`
    ],
    result: questResult.result
  };
}

export function currentActivityText(
  game: Pick<
    GameState,
    | "phase"
    | "current_round"
    | "failed_team_votes"
    | "speech_order"
    | "speeches"
    | "winner"
  >,
  playerNames: ReadonlyMap<string, string>
): string {
  if (game.winner || game.phase === "complete") {
    return "对局已结束";
  }
  const roundPrefix = `第 ${game.current_round} 轮任务`;
  const attemptNumber = game.failed_team_votes + 1;
  if (game.phase === "team_proposal") {
    return `${roundPrefix}，第 ${attemptNumber} 次组队中`;
  }
  if (game.phase === "speech") {
    const nextSpeakerId = game.speech_order[Object.keys(game.speeches).length];
    return nextSpeakerId ? `${nameFor(playerNames, nextSpeakerId)}发言中` : "发言中";
  }
  if (game.phase === "voting") {
    return `${roundPrefix}，第 ${attemptNumber} 次组队投票中`;
  }
  if (game.phase === "quest") {
    return `${roundPrefix}，任务投票中`;
  }
  if (game.phase === "assassination") {
    return "刺杀中";
  }
  if (game.phase === "lady_of_lake") {
    return "湖中仙女查看中";
  }
  return phaseFallbackLabel(game.phase);
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

function valueAsOptionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const numberValue = Number(value);
    return Number.isFinite(numberValue) ? numberValue : null;
  }
  return null;
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

function phaseFallbackLabel(phase: string): string {
  const labels: Record<string, string> = {
    team_proposal: "组队中",
    speech: "发言中",
    voting: "组队投票中",
    quest: "任务投票中",
    assassination: "刺杀中",
    lady_of_lake: "湖中仙女查看中",
    complete: "对局已结束"
  };
  return labels[phase] ?? `${phase}进行中`;
}
