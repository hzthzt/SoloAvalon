# Room List And AI Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build room-style multi-game navigation, deterministic `游戏#Num` game ids, paused-error status, and ended-room AI usage review.

**Architecture:** Reuse `games` as the room source of truth and extend existing AI decision persistence with nullable usage fields. Add service-level summary helpers so the frontend can render ended-room decision details and aggregate token/cache statistics without inventing client-side business rules.

**Tech Stack:** Python backend with SQLite repositories and unittest coverage; FastAPI-compatible API layer; React + TypeScript frontend built by Vite.

---

### Task 1: Backend Room IDs And Status

**Files:**
- Modify: `backend/app/storage/game_repository.py`
- Modify: `backend/app/services/game_service.py`
- Test: `tests/services/test_game_service.py`
- Test: `tests/storage/test_game_repository.py`

- [ ] **Step 1: Add failing tests for `游戏#Num` IDs**

Add tests that create games in an empty database and after seeded legacy IDs. Assert new IDs are `游戏#1`, `游戏#2`, and ignore old timestamp-style IDs.

- [ ] **Step 2: Implement next room ID lookup**

Add a repository helper that reads existing ids matching `游戏#%`, extracts numeric suffixes safely, and returns the next ID. Use it from `GameService.create_game` instead of `_timestamp_game_id()`.

- [ ] **Step 3: Add paused status transitions**

When AI decision handling raises `AiDecisionError`, set `games.status` to `error_paused`. When a later action successfully advances a non-complete game, persist `active`. Preserve `complete` once `winner` is present.

- [ ] **Step 4: Run focused backend tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.services.test_game_service tests.storage.test_game_repository -v
```

Expected: all tests pass.

### Task 2: AI Usage Capture And Persistence

**Files:**
- Modify: `backend/app/llm/provider.py`
- Modify: `backend/app/ai/player.py`
- Modify: `backend/app/storage/schema.py`
- Modify: `backend/app/storage/database.py`
- Modify: `backend/app/storage/ai_decision_repository.py`
- Modify: `backend/app/services/game_service.py`
- Test: `tests/llm/test_profiles.py` or `tests/ai/test_prompting_and_provider.py`
- Test: `tests/storage/test_ai_decision_repository.py`
- Test: `tests/services/test_game_service.py`

- [ ] **Step 1: Add failing provider usage test**

Verify an OpenAI-compatible response returns content plus usage fields: prompt, completion, total, cached, and computed cache hit rate. Verify missing usage returns `None` fields.

- [ ] **Step 2: Introduce a typed completion result**

Return a result object from `LlmProvider.chat_completion` containing `content`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cached_tokens`, and `cache_hit_rate`. Keep parsing tolerant of providers that omit usage.

- [ ] **Step 3: Propagate usage through AI turn results**

Add nullable usage fields to `AiTurnResult` and `AiDecisionError`. Save those values when logging successful or failed AI decisions.

- [ ] **Step 4: Persist nullable usage columns**

Extend schema and migration initialization so old SQLite files get the five new nullable `ai_decisions` columns. Update repository dataclasses, insert statements, and row mapping.

- [ ] **Step 5: Run focused usage tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.ai.test_prompting_and_provider tests.storage.test_ai_decision_repository -v
```

Expected: all tests pass.

### Task 3: Room Detail API And Aggregates

**Files:**
- Modify: `backend/app/services/game_service.py`
- Modify: `backend/app/api/games.py`
- Test: `tests/api/test_games_api.py`
- Test: `tests/services/test_game_service.py`

- [ ] **Step 1: Add failing API tests**

Assert `GET /api/games/{game_id}/room` or equivalent service method returns game state, full events, AI decisions, per-player usage summaries, and per-model usage summaries. Assert aggregate cache hit rate averages only non-null values.

- [ ] **Step 2: Implement room detail service helper**

Build a method that combines `get_game_state`, full event export, `AiDecisionRepository.list_decisions`, and persisted players. Include player names in per-player summaries.

- [ ] **Step 3: Expose the room detail API**

Add a route under existing games API. Keep private decision details available from this endpoint; frontend will only render them for ended rooms.

- [ ] **Step 4: Run focused API tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.api.test_games_api -v
```

Expected: all tests pass.

### Task 4: Frontend Room Types And Rendering

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.ts`

- [ ] **Step 1: Add failing frontend tests**

Cover room tab label, Chinese status display, create-and-enter behavior, ended-room AI detail expansion, and missing usage as `暂无数据`.

- [ ] **Step 2: Add room detail types and API client**

Add TypeScript types for AI decision detail, usage summary, and room detail response. Add `getRoomDetail(gameId)`.

- [ ] **Step 3: Rename logs tab to rooms**

Change the tab from “日志” to “房间”. Use existing game summaries as the room list and map statuses to the required Chinese labels.

- [ ] **Step 4: Enter newly created rooms immediately**

After `createGame`, set the returned game as current, refresh rooms, select that room, and render its play/detail view without deleting or replacing other rooms.

- [ ] **Step 5: Render ended-room AI usage**

For complete rooms, show per-player and per-model summary tables plus AI decision details under `ai_decision` events. Hide private details for active and `error_paused` rooms.

- [ ] **Step 6: Run frontend tests and build**

Run:

```powershell
cd frontend
npm test -- --run
npm run build
```

Expected: tests and build pass.

### Task 5: Full Verification

**Files:**
- No new source files expected.

- [ ] **Step 1: Run full backend suite**

Run:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: build passes.

- [ ] **Step 3: Inspect git diff**

Run:

```powershell
git status --short
git diff --stat
```

Expected: only planned backend, frontend, tests, and plan files changed.
