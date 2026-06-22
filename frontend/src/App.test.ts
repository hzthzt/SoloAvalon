import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("start game UI does not expose seed controls", () => {
  assert.equal(source.includes("Seed"), false);
  assert.equal(source.includes("setSeed"), false);
});

test("start game request does not send seed", () => {
  assert.equal(source.includes("seed:"), false);
});

test("event log renders expandable AI prompt details", () => {
  assert.equal(source.includes("AiPromptDetails"), true);
  assert.equal(source.includes("prompt_messages"), true);
  assert.equal(source.includes("<details"), true);
});

test("rooms replace logs and load room details", () => {
  assert.equal(source.includes('type Tab = "game" | "profiles" | "rooms"'), true);
  assert.equal(source.includes("房间"), true);
  assert.equal(source.includes("getRoomDetail"), true);
  assert.equal(apiSource.includes("RoomDetail"), true);
});

test("game summaries expose archive metadata and archive api", () => {
  assert.equal(apiSource.includes("archived_at: string | null"), true);
  assert.equal(apiSource.includes("display_name: string"), true);
  assert.equal(apiSource.includes("archiveGame"), true);
  assert.equal(apiSource.includes("/archive"), true);
});

test("room display names are separate from internal game ids", () => {
  assert.equal(source.includes("{summary.display_name}"), true);
  assert.equal(source.includes("<h3>{summary.id}</h3>"), false);
  assert.equal(source.includes("enterPlayableRoom(summary.id)"), true);
  assert.equal(source.includes("archiveRoom(summary.id)"), true);
});

test("game id route segments are encoded for room ids containing hash", () => {
  assert.equal(apiSource.includes("gamePath(gameId)"), true);
  assert.equal(apiSource.includes("encodeURIComponent(gameId)"), true);
  assert.equal(apiSource.includes("`/api/games/${encodeURIComponent(gameId)}`"), true);
});

test("play view owns unarchived room list and enters rooms for play", () => {
  assert.equal(source.includes("playableRooms"), true);
  assert.equal(source.includes("archivedRooms"), true);
  assert.equal(source.includes("enterPlayableRoom"), true);
  assert.equal(source.includes("archiveRoom"), true);
  assert.equal(source.includes("游玩房间"), true);
});

test("playable room list is centered while setup panel keeps new room controls", () => {
  assert.equal(source.includes("<h2>开局</h2>"), true);
  assert.equal(source.includes("PlayableRoomList"), true);
  assert.equal(
    source.indexOf("<PlayableRoomList") > source.indexOf("</section>\r\n\r\n          {game &&"),
    true
  );
  assert.equal(source.includes("<h2>新建房间</h2>"), false);
});

test("playable room cards constrain long room names inside the list", () => {
  assert.equal(styleSource.includes(".room-list-item h3"), true);
  assert.equal(styleSource.includes("overflow-wrap: anywhere"), true);
  assert.equal(styleSource.includes("min-width: 0"), true);
});

test("creating a game enters the new room without removing other rooms", () => {
  assert.equal(source.includes("setSelectedRoomGameId(created.id)"), true);
  assert.equal(apiSource.includes("display_name: string"), true);
  assert.equal(source.includes("setTab(\"game\")"), true);
  assert.equal(source.includes("setGames(gameList)"), true);
});

test("active play view can return to room list without stopping the room", () => {
  assert.equal(source.includes("leaveActiveRoom"), true);
  assert.equal(source.includes("返回房间"), true);
  assert.equal(source.includes("onLeaveRoom"), true);
  assert.equal(source.includes("setGame(null);"), true);
  assert.equal(source.includes("setActiveGameEvents([]);"), true);
  assert.equal(source.includes("setActiveRoomDetail(null);"), true);
  assert.equal(source.includes("await refreshLists();"), true);
  assert.equal(source.includes("archiveGame(game.id)"), false);
  assert.equal(source.includes("deleteGame(game.id)"), false);
});

test("archive view only renders archived rooms as replay records", () => {
  assert.equal(source.includes("archivedRooms.map"), true);
  assert.equal(source.includes("games.map((summary)"), false);
  assert.equal(source.includes("showEvents(summary.id)"), true);
});

test("ended rooms show ai usage summaries and missing usage fallback", () => {
  assert.equal(source.includes("AiUsageSummary"), true);
  assert.equal(source.includes("usage_by_player"), true);
  assert.equal(source.includes("usage_by_model"), true);
  assert.equal(source.includes("暂无数据"), true);
});

test("ended active game loads room details for private information feed decisions", () => {
  assert.equal(source.includes("activeRoomDetail"), true);
  assert.equal(source.includes("refreshActiveRoomDetailIfComplete"), true);
  assert.equal(source.includes("setActiveRoomDetail(null)"), true);
  assert.equal(source.includes("roomDetailForActiveReview"), true);
});

test("active information feed renders expandable ai decision summaries only after completion", () => {
  assert.equal(source.includes("InformationFeedDecisionDetails"), true);
  assert.equal(source.includes("decisionsByFeedItem"), true);
  assert.equal(source.includes("AI 决策摘要"), true);
  assert.equal(source.includes("查看完整 Prompt 与回答"), true);
});

test("team proposal feed matches captain decisions even after phase advances", () => {
  assert.equal(source.includes("latestDecisionBeforeEventByType"), true);
  assert.equal(source.includes('"team_proposal"'), true);
});

test("ai decision full details fall back to private event payload fields", () => {
  assert.equal(source.includes("decisionOutputRaw"), true);
  assert.equal(source.includes("decisionOutputParsed"), true);
  assert.equal(source.includes("decisionNumberMetric"), true);
  assert.equal(source.includes("event.private_payload?.output_raw"), true);
});

test("start game requires a real model profile instead of fallback", () => {
  assert.equal(source.includes("Fallback"), false);
  assert.equal(source.includes("请选择模型"), true);
  assert.equal(source.includes("!defaultProfileId"), true);
});

test("profile list shows runtime configuration fields", () => {
  assert.equal(source.includes("profile.id"), true);
  assert.equal(source.includes("profile.base_url"), true);
  assert.equal(source.includes("profile.model"), true);
});

test("model profile form exposes timeout retry configuration", () => {
  assert.equal(source.includes("timeout_retries: 5"), true);
  assert.equal(source.includes("profile.timeout_retries"), true);
  assert.equal(apiSource.includes("timeout_retries: number"), true);
});

test("ai decision gateway errors offer manual retry", () => {
  assert.equal(source.includes("manualRetryRepeat"), true);
  assert.equal(source.includes("error.status === 502 || error.status === 504"), true);
  assert.equal(source.includes("手动重试"), true);
});

test("error paused games expose a generic retry advance action", () => {
  assert.equal(apiSource.includes("retryPausedGame"), true);
  assert.equal(apiSource.includes("/retry"), true);
  assert.equal(source.includes('game.status === "error_paused"'), true);
  assert.equal(source.includes("sendPausedGameRetry"), true);
  assert.equal(source.includes("重试推进"), true);
});

test("api errors include structured backend error details and tracebacks", () => {
  assert.equal(apiSource.includes("formatErrorDetail"), true);
  assert.equal(apiSource.includes("error_type"), true);
  assert.equal(apiSource.includes("traceback"), true);
});

test("model edit form does not display plain api key from saved profile", () => {
  assert.equal(source.includes("api_key: profile.api_key"), false);
  assert.equal(source.includes('type="password"'), false);
  assert.equal(source.includes("profile.api_key_masked"), true);
  assert.equal(source.includes("留空则保留原密钥"), true);
});

test("player cards show original names while start request sends human name", () => {
  assert.equal(source.includes("human_name: humanName"), true);
  assert.equal(source.includes("player.original_name"), true);
});

test("player cards keep legal visible roles before final reveal", () => {
  assert.equal(source.includes("playerRoleText(player)"), true);
  assert.equal(source.includes("player.revealed_role ?? player.visible_role"), true);
});

test("player cards label Percival merlin candidates", () => {
  assert.equal(source.includes('unknown_merlin: "梅林候选"'), true);
});

test("start game request sends player count and enabled options", () => {
  assert.equal(source.includes("player_count: playerCount"), true);
  assert.equal(source.includes("enabled_options: enabledOptions"), true);
  assert.equal(source.includes("setPlayerCount"), true);
  assert.equal(apiSource.includes("player_count?: number"), true);
  assert.equal(apiSource.includes("enabled_options?: string[]"), true);
});

test("game view uses backend mission metadata and dynamic player counts", () => {
  assert.equal(source.includes("game.missions"), true);
  assert.equal(source.includes("game.players.length"), true);
  assert.equal(source.includes("missionSizes"), false);
});

test("lady of lake action is available in the frontend", () => {
  assert.equal(source.includes('activeAction === "use_lady_of_lake"'), true);
  assert.equal(source.includes("lady_of_lake_eligible_target_ids"), true);
  assert.equal(source.includes("lady_of_lake_known_factions"), true);
});

test("action panel owns the persistent round summaries", () => {
  assert.equal(source.includes("ActionRoundSummary"), true);
  assert.equal(source.includes("summaries.slice(-3)"), false);
  assert.equal(source.indexOf("<ActionRoundSummary"), source.lastIndexOf("<ActionRoundSummary"));
});

test("broadcasts render as centered announcement bubbles", () => {
  assert.equal(source.includes("announcement-item"), true);
  assert.equal(source.includes("announcement-bubble"), true);
  assert.equal(
    source.indexOf("<time>{formatDateTime(row.createdAt)}</time>") <
      source.indexOf("<p>{row.text}</p>"),
    true
  );
});

test("announcement bubbles use readable rounded rectangles", () => {
  assert.equal(styleSource.includes(".announcement-bubble {\n  background: #eef3f8;\n  border: 1px solid #d9e1ea;\n  border-radius: 8px;"), true);
  assert.equal(styleSource.includes(".announcement-bubble {\n  background: #eef3f8;\n  border: 1px solid #d9e1ea;\n  border-radius: 999px;"), false);
});

test("announcement ai decision details are left aligned", () => {
  assert.equal(styleSource.includes(".feed-decision-details {\n  border-top: 1px solid rgba(25, 50, 77, 0.12);\n  justify-self: stretch;"), true);
  assert.equal(styleSource.includes("  text-align: left;\n  width: 100%;"), true);
  assert.equal(styleSource.includes(".feed-decision-card {\n  background: #f8fafc;\n  border: 1px solid #d9e1ea;\n  border-radius: 8px;\n  display: grid;\n  gap: 8px;\n  justify-items: stretch;"), true);
});

test("expanded announcement details can widen without resizing the feed layout", () => {
  assert.equal(styleSource.includes("  box-sizing: border-box;\n  color: #3c4654;"), true);
  assert.equal(styleSource.includes("  width: min(100%, 520px);"), true);
  assert.equal(styleSource.includes(".announcement-bubble:has(.feed-decision-details[open]) {\n  max-width: min(100%, 760px);\n  width: min(100%, 760px);\n}"), true);
  assert.equal(styleSource.includes(".feed-decision-details {\n  border-top: 1px solid rgba(25, 50, 77, 0.12);\n  justify-self: stretch;\n  margin-top: 8px;\n  max-width: 100%;\n  min-width: 0;"), true);
  assert.equal(styleSource.includes(".prompt-message pre {\n  background: #f3f6fa;\n  border-radius: 8px;\n  color: #3c4654;\n  margin: 0;\n  max-width: 100%;\n  min-width: 0;\n  overflow: auto;\n  overflow-wrap: anywhere;\n  padding: 10px;\n  text-align: left;\n  white-space: pre-wrap;"), true);
});

test("game desk title includes current team attempt", () => {
  assert.equal(source.includes("teamAttemptNumber"), true);
  assert.equal(source.includes("第 {game.current_round} 轮任务 · 第 {teamAttemptNumber} 次组队"), true);
});

test("start game shows a playing panel before state events arrive", () => {
  assert.equal(source.includes("startingGame"), true);
  assert.equal(source.includes("StartingGameDesk"), true);
});

test("information feed reveals queued items gradually", () => {
  assert.equal(source.includes("visibleItemCount"), true);
  assert.equal(source.includes("setTimeout"), true);
});

test("active game subscribes to server-sent event updates while the play view is open", () => {
  assert.equal(source.includes("subscribeGameEvents"), true);
  assert.equal(source.includes("new EventSource"), true);
  assert.equal(source.includes("eventSource.addEventListener(\"game-event\""), true);
  assert.equal(source.includes("eventSource.close()"), true);
  assert.equal(source.includes("window.setInterval"), false);
});

test("api exposes an sse event stream url for active game events", () => {
  assert.equal(apiSource.includes("gameEventsStreamUrl"), true);
  assert.equal(apiSource.includes("/events/stream"), true);
});

test("information feed keeps the scroll pinned when already at the bottom", () => {
  assert.equal(source.includes("feedListRef"), true);
  assert.equal(source.includes("wasFeedAtBottomRef"), true);
  assert.equal(source.includes("scrollTop = element.scrollHeight"), true);
  assert.equal(source.includes("onScroll={rememberFeedScrollPosition}"), true);
});

test("information feed occupies the full center column", () => {
  assert.equal(styleSource.includes("grid-template-columns: minmax(0, 1fr) minmax(220px, 280px);"), false);
  assert.equal(styleSource.includes(".game-flow-layout {\n  align-items: start;\n  display: grid;\n  gap: 12px;\n  grid-template-columns: minmax(0, 1fr);"), true);
});
