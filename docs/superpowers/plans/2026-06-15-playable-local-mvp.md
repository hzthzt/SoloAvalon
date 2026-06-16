# Playable Local MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete a local single-player Avalon MVP that can run a 5-player game with one human, four AI seats, OpenAI-compatible profile configuration, event logs, export, and a React desktop.

**Architecture:** Keep the deterministic rules engine pure. Add AI, prompt, and runtime service modules around it, then expose that service through FastAPI. The React app talks only to filtered API payloads and never receives hidden roles while a game is active.

**Tech Stack:** Python 3.10, FastAPI, standard-library SQLite, standard-library unittest, React 18, TypeScript, Vite.

---

## File Structure

- `backend/app/ai/`: context building, fallback strategy, decision orchestration, and AI memory records.
- `backend/app/prompting/`: stable prompt templates and schema contracts.
- `backend/app/llm/provider.py`: OpenAI-compatible Chat Completions client with injectable transport for tests.
- `backend/app/services/game_service.py`: in-memory local game runtime, persistence bridge, AI automation, and event logging.
- `backend/app/api/`: FastAPI routers and request/response models for games, actions, logs, and LLM profiles.
- `frontend/`: Vite React app with pages for new game setup, play desk, LLM profile management, and log review.

### Task 1: AI Context, Prompting, and Fallback Decisions

**Files:**
- Create: `backend/app/ai/context.py`
- Create: `backend/app/ai/strategy.py`
- Create: `backend/app/ai/player.py`
- Create: `backend/app/prompting/templates.py`
- Create: `backend/app/prompting/schemas.py`
- Create: `backend/app/llm/provider.py`
- Test: `tests/ai/test_context_builder.py`
- Test: `tests/ai/test_strategy.py`
- Test: `tests/ai/test_prompting_and_provider.py`

- [ ] Write failing tests proving AI prompts use private views, stable prefixes are deterministic, illegal model output falls back safely, and fallback strategy can propose teams, speak, vote, choose quest actions, and assassinate.
- [ ] Run the focused AI tests and confirm they fail because modules are missing.
- [ ] Implement the AI context builder, prompt templates, schema parsing, injectable LLM provider, and deterministic fallback strategy.
- [ ] Run the focused AI tests and full backend test suite.

### Task 2: Local Game Service and FastAPI Routes

**Files:**
- Create: `backend/app/services/game_service.py`
- Create: `backend/app/api/games.py`
- Create: `backend/app/api/llm_profiles.py`
- Create: `backend/app/api/models.py`
- Modify: `backend/app/main.py`
- Test: `tests/services/test_game_service.py`
- Test: `tests/api/test_games_api.py`
- Test: `tests/api/test_llm_profiles_api.py`

- [ ] Write failing tests for creating a game, loading a human-filtered state, submitting human actions, AI auto-advancement, listing/deleting/exporting logs, and LLM profile CRUD/test routes.
- [ ] Run the service/API tests and confirm they fail because service and routes are missing.
- [ ] Implement service state serialization, event logging, AI automation around human turns, and FastAPI routers.
- [ ] Run focused service/API tests and full backend test suite.

### Task 3: React Frontend

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/styles.css`

- [ ] Add a Vite React application with TypeScript checks.
- [ ] Implement setup, game desk, model profile manager, and log/export views.
- [ ] Connect actions to FastAPI and render only API-provided state.
- [ ] Run `npm install`, `npm run build`, and local browser verification.

### Task 4: End-to-End Verification

**Files:**
- Modify: `README.md`

- [ ] Document backend and frontend run commands.
- [ ] Run `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
- [ ] Run `npm run build` in `frontend`.
- [ ] Start FastAPI and Vite locally, open the app, and verify a game can advance through at least setup, team proposal, speech, voting, and quest submission.
