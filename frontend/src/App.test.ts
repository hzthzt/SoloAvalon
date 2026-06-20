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
