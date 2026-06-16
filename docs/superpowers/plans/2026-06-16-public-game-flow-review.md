# Public Game Flow Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent public knowledge board, system broadcast stream, and complete log replay from public game events.

**Architecture:** Keep backend visibility boundaries unchanged. Add a pure frontend event aggregation module that converts public `GameEvent[]` into structured round summaries and broadcast rows, then reuse it from both the active game page and logs page.

**Tech Stack:** React 18, TypeScript, Vite, Node built-in test runner, existing FastAPI public game events.

---

## File Structure

- Create `frontend/src/flowReview.ts`: pure event aggregation and formatting helpers.
- Create `frontend/src/flowReview.test.ts`: Node test-runner tests for public-history behavior.
- Create `frontend/tsconfig.test.json`: TypeScript config that emits frontend unit tests to `.test-dist`.
- Modify `frontend/package.json`: add a `test` script using `tsc` plus Node's built-in test runner.
- Modify `frontend/src/App.tsx`: render game page summary/broadcast and logs page replay using `flowReview.ts`.
- Modify `frontend/src/styles.css`: add compact board, broadcast, and replay styling.

## Task 1: Test Harness And Failing Aggregator Tests

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/tsconfig.test.json`
- Create: `frontend/src/flowReview.test.ts`

- [ ] **Step 1: Add TypeScript test config**

Create `frontend/tsconfig.test.json`:

```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "noEmit": false,
    "outDir": ".test-dist",
    "types": ["node"]
  },
  "include": ["src/flowReview.ts", "src/flowReview.test.ts"]
}
```

- [ ] **Step 2: Add test script**

Add this script in `frontend/package.json`:

```json
"test": "tsc --project tsconfig.test.json && node --test .test-dist/flowReview.test.js"
```

- [ ] **Step 3: Write failing tests**

Create `frontend/src/flowReview.test.ts` using `node:test` and `node:assert/strict`. Import `buildFlowReview` from `./flowReview.js` and assert:

```ts
const review = buildFlowReview(events, playerNames);
expect(review.rounds[0].team?.members).toEqual(["You", "AI 1"]);
expect(review.rounds[0].speeches[0].message).toContain("支持");
expect(review.rounds[0].vote).toBeUndefined();
expect(review.broadcasts.some((row) => row.text.includes("提交车队"))).toBe(true);
```

Add a second test where `vote_result` appears and assert:

```ts
expect(review.rounds[0].vote?.approvals).toEqual(["You", "AI 2", "AI 4"]);
expect(review.rounds[0].vote?.rejections).toEqual(["AI 1", "AI 3"]);
expect(review.broadcasts.some((row) => row.text.includes("投票公开"))).toBe(true);
```

- [ ] **Step 4: Verify RED**

Run: `npm test`

Expected: FAIL because `./flowReview` does not exist yet.

## Task 2: Implement Pure Flow Aggregation

**Files:**
- Create: `frontend/src/flowReview.ts`
- Test: `frontend/src/flowReview.test.ts`

- [ ] **Step 1: Create types**

Define:

```ts
export type FlowReview = {
  rounds: ReviewRound[];
  broadcasts: BroadcastRow[];
};
```

with `ReviewRound` fields for round number, team, speeches, vote, quest result, and assassination.

- [ ] **Step 2: Implement `buildFlowReview`**

Iterate public events in event order. Start with round 1, create a team entry on `team_proposed`, append speeches on `speech`, collect pending votes on `vote_cast`, reveal them only on `vote_result`, advance round on `quest_result` when the next phase is not complete/assassination, and attach assassination on `assassination`.

- [ ] **Step 3: Implement broadcasts**

Generate readable rows for team proposals, speeches, vote reveal, quest results, and assassination. Do not generate individual vote-value broadcasts before `vote_result`.

- [ ] **Step 4: Verify GREEN**

Run: `npm test`

Expected: PASS.

## Task 3: Integrate Game Page Summary And Broadcast

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Import aggregator**

Import `buildFlowReview` and use it inside `GameDesk`.

- [ ] **Step 2: Replace simple event list**

Replace the current `GameFlow` event list with:

- `PublicKnowledgeBoard`: compact round cards.
- `SystemBroadcast`: recent broadcast rows.

- [ ] **Step 3: Verify build**

Run: `npm run build`

Expected: PASS.

## Task 4: Integrate Logs Page Complete Replay

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Store event player names**

When logs are loaded, derive player names from the active game if it matches or from event payloads as a fallback.

- [ ] **Step 2: Render replay before raw events**

In logs detail, render `ReplayDetail` above the raw event stream. It should show all rounds chronologically, including teams, speeches, revealed votes, quest results, and assassination.

- [ ] **Step 3: Verify build**

Run: `npm run build`

Expected: PASS.

## Task 5: Full Verification And Commit

**Files:**
- All changed files.

- [ ] **Step 1: Run frontend tests**

Run: `npm test`

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 3: Run backend tests**

Run: `python -m unittest discover`

Expected: PASS.

- [ ] **Step 4: Commit**

Commit all implementation files with:

```bash
git commit -m "Add public game flow review"
```
