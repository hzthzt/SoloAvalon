import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");

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

test("model edit form displays plain api key from saved profile", () => {
  assert.equal(source.includes("api_key: profile.api_key"), true);
  assert.equal(source.includes('type="password"'), false);
  assert.equal(source.includes("profile.api_key"), true);
});

test("player cards show original names while start request sends human name", () => {
  assert.equal(source.includes("human_name: humanName"), true);
  assert.equal(source.includes("player.original_name"), true);
});
