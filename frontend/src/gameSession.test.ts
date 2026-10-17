import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, newerGameState, submitAction, submitHumanAiAction } from "./api.js";
import type { GameEvent, GameState } from "./api.js";
import { subscribeGameEvents, waitForGameIdle } from "./gameSession.js";

const state = (revision: number, advancing = false, overrides: Partial<GameState> = {}): GameState => ({
  id: "game_1", revision, is_advancing: advancing, status: "active", phase: "speech",
  next_human_action: "speak", ...overrides
} as GameState);

test("older HTTP or SSE states cannot replace a newer revision", () => {
  const current = state(4);
  assert.equal(newerGameState(current, state(3)), current);
  assert.equal(newerGameState(null, current), current);
  assert.equal(newerGameState(current, state(5)).revision, 5);
});

test("accepted human AI intent waits even while next human action remains visible", async () => {
  const snapshots = [state(3, true), state(2, true), state(4)];
  const applied: number[] = [];
  const result = await waitForGameIdle(state(2, true), async () => snapshots.shift()!, async () => {},
    async (update) => { applied.push(update.revision); return update; }, () => true);
  assert.deepEqual(applied, [3, 3, 4]);
  assert.equal(result.revision, 4);
  assert.equal(result.is_advancing, false);
});

test("waiting stops on error pause before the next human request", async () => {
  let reads = 0;
  const result = await waitForGameIdle(state(2, true), async () => {
    reads += 1;
    return state(3, false, { status: "error_paused" });
  }, async () => {}, async (update) => update, () => true);
  assert.equal(reads, 1);
  assert.equal(result.status, "error_paused");
});

test("leaving a room cancels polling before applying a late response", async () => {
  let active = true;
  let applied = false;
  await waitForGameIdle(state(2, true), async () => { active = false; return state(3); },
    async () => {}, async (update) => { applied = true; return update; }, () => active);
  assert.equal(applied, false);
});

test("SSE routes public events and revision states independently and closes its source", () => {
  const handlers = new Map<string, (event: MessageEvent) => void>();
  const events: GameEvent[] = [];
  const states: GameState[] = [];
  let requestedUrl = "";
  let closed = false;
  const close = subscribeGameEvents("game_1", 7, (event) => events.push(event), (update) => states.push(update), (url) => {
    requestedUrl = url;
    return {
      addEventListener: (name: string, listener: (event: MessageEvent) => void) => { handlers.set(name, listener); },
      close: () => { closed = true; }
    };
  });
  const emit = (name: string, data: string) => {
    const handler = handlers.get(name)!;
    handler({ data } as MessageEvent);
  };
  emit("game-state", JSON.stringify(state(10, true)));
  emit("game-event", JSON.stringify({ event_index: 8, event_type: "speech" }));
  emit("game-event", "invalid JSON");
  emit("game-state", JSON.stringify(state(11)));
  close();
  assert.equal(requestedUrl, "/api/games/game_1/events/stream?after=7");
  assert.deepEqual(events.map((event) => event.event_index), [8]);
  assert.deepEqual(states.map((update) => update.revision), [10, 11]);
  assert.equal(closed, true);
});

test("action requests carry revisions and do not replay a conflict", async () => {
  const original = globalThis.fetch;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify(calls.length === 3 ? { detail: { message: "state changed", current_revision: 9 } } : state(8, true)),
      { status: calls.length === 3 ? 409 : 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    await submitAction("game_1", { action_type: "speak", message: "hello" }, 6);
    await submitHumanAiAction("game_1", 7);
    let error: unknown;
    try { await submitHumanAiAction("game_1", 7); } catch (failure) { error = failure; }
    assert.equal(error instanceof ApiError && error.status, 409);
    assert.deepEqual(calls.map((call) => call.body.expected_revision), [6, 7, 7]);
    assert.equal(calls[0].body.action_type, "speak");
    assert.equal(calls.length, 3);
  } finally { globalThis.fetch = original; }
});
