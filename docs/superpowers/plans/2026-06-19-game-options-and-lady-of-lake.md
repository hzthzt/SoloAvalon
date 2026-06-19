# Game Options And Lady Of Lake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 5-10 人开局、每局可选项开关，以及 8-10 人局可用的完整湖中仙女流程。

**Architecture:** 后端规则层新增通用建局、每局 `GameOption` 与 `LADY_OF_LAKE` 阶段；服务层负责事件、AI 自动推进、状态恢复和公开视图；前端只提交开关并渲染后端返回的任务配置、合法目标和私有查验结果。Prompt 开关由每局状态驱动，避免把未启用机制发送给模型。

**Tech Stack:** Python dataclasses/unittest/SQLite/FastAPI；React + TypeScript + Vite；现有事件流和 prompt 模板。

---

### Task 1: 规则层多人数建局与开关校验

**Files:**
- Modify: `backend/app/game/models.py`
- Modify: `backend/app/game/rules.py`
- Test: `tests/game/test_rules_setup.py`

- [ ] **Step 1: Write failing tests**

Add tests covering `create_game(player_count=5..10)`, `GameOption.TRISTAN_ISOLDE` rejection for 5-8, successful replacement for 9-10, and `GameOption.LADY_OF_LAKE` rejection for 5-7.

- [ ] **Step 2: Run tests to verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.game.test_rules_setup -v
```

Expected: FAIL because `create_game` and `GameOption` do not exist.

- [ ] **Step 3: Implement minimal rules support**

Add `GameOption`, `LadyOfLakeInspection`, `Phase.LADY_OF_LAKE`, `GameState.enabled_options`, lake state fields, and `create_game()`. Keep `create_five_player_game()` as a compatibility wrapper.

- [ ] **Step 4: Re-run rules setup tests**

Run the command from Step 2. Expected: PASS.

---

### Task 2: 湖中仙女规则动作与私有视角

**Files:**
- Modify: `backend/app/game/models.py`
- Modify: `backend/app/game/rules.py`
- Test: `tests/game/test_quest_and_endgame.py`
- Test: `tests/game/test_rules_setup.py`

- [ ] **Step 1: Write failing tests**

Add tests proving lake phase triggers after quest 2/3/4 only when enabled, skips when game moves to assassination/complete, rejects invalid targets, transfers holder to target, and only the viewer sees `lady_of_lake_known_factions`.

- [ ] **Step 2: Run tests to verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.game.test_quest_and_endgame tests.game.test_rules_setup -v
```

Expected: FAIL because `use_lady_of_lake()` and private-view lake results do not exist.

- [ ] **Step 3: Implement lake rules**

Add `use_lady_of_lake()`, `eligible_lady_of_lake_target_ids()`, trigger logic in `finalize_quest()`, and private-view lake result filtering.

- [ ] **Step 4: Re-run game tests**

Run the command from Step 2. Expected: PASS.

---

### Task 3: API、存储、服务层与状态恢复

**Files:**
- Modify: `backend/app/api/models.py`
- Modify: `backend/app/services/game_service.py`
- Modify: `backend/app/game/events.py`
- Modify: `backend/app/storage/schema.py`
- Modify: `backend/app/storage/database.py`
- Modify: `backend/app/storage/game_repository.py`
- Test: `tests/api/test_api_models.py`
- Test: `tests/services/test_game_service.py`
- Test: `tests/storage/test_game_repository.py`

- [ ] **Step 1: Write failing tests**

Add tests for `CreateGameRequest.player_count/enabled_options`, persistence of `enabled_options`, service response fields `missions`, `enabled_options`, `lady_of_lake_*`, human lake action, `lady_of_lake_used` payloads, and restore replay.

- [ ] **Step 2: Run tests to verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.api.test_api_models tests.services.test_game_service tests.storage.test_game_repository -v
```

Expected: FAIL because API/storage/service do not expose the new fields.

- [ ] **Step 3: Implement integration**

Parse and validate create-game payloads, persist `enabled_options`, include new public state fields, add `use_lady_of_lake` human and AI service branches, append/replay `lady_of_lake_used`, and restore missions by player count.

- [ ] **Step 4: Re-run integration tests**

Run the command from Step 2. Expected: PASS.

---

### Task 4: AI prompt、schemas 与事件复盘

**Files:**
- Modify: `backend/app/ai/context.py`
- Modify: `backend/app/ai/player.py`
- Modify: `backend/app/ai/strategy.py`
- Modify: `backend/app/prompting/schemas.py`
- Modify: `backend/app/prompting/templates.py`
- Modify: `config.example/prompt_templates.json`
- Test: `tests/ai/test_context_builder.py`
- Test: `tests/ai/test_prompting_and_provider.py`

- [ ] **Step 1: Write failing AI tests**

Add tests that disabled lake/tristan/detail prompts are absent, enabled per-game options appear, lake private result is visible only to viewer, and lake target JSON is parsed and validated.

- [ ] **Step 2: Run tests to verify failures**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.ai.test_context_builder tests.ai.test_prompting_and_provider -v
```

Expected: FAIL because prompt/options are still global or missing lake action support.

- [ ] **Step 3: Implement AI support**

Drive optional mechanics from `state.enabled_options`, add lake legal action, strategy dataclass, parser, prompt action, and AI player method.

- [ ] **Step 4: Re-run AI tests**

Run the command from Step 2. Expected: PASS.

---

### Task 5: 前端开局选项、动态人数与湖女操作

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/flowReview.ts`
- Modify: `frontend/src/flowReview.test.ts`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.ts`

- [ ] **Step 1: Write failing frontend tests**

Add tests or type-level coverage for missions from API, dynamic player count, role labels, and `lady_of_lake_used` flow review without public faction leakage.

- [ ] **Step 2: Run tests to verify failures**

Run:

```powershell
cd frontend
npm test -- App.test.ts flowReview.test.ts
```

Expected: FAIL because frontend still assumes 5-player mission sizes and lacks lake event rendering.

- [ ] **Step 3: Implement frontend**

Add create payload fields, dynamic AI arrays, option toggles with disabled ranges, mission/team size from `game.missions`, dynamic vote counts, lake target selector, private lake result display, role labels, and flow-review lake event text.

- [ ] **Step 4: Re-run frontend tests and build**

Run:

```powershell
cd frontend
npm test -- App.test.ts flowReview.test.ts
npm run build
```

Expected: PASS.

---

### Task 6: 完整验证与收尾

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run backend test suite**

Run:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 3: Review diff and commit**

Run:

```powershell
git diff --stat
git status --short
```

Expected: only feature-related files changed. Commit with a Chinese Conventional Commit message.
