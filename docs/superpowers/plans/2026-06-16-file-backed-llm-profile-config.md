# File-Backed LLM Profile Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store LLM profile configuration in ignored plaintext JSON at `config/llm_profiles.json` instead of SQLite, and remove `max_tokens` from user-facing model configuration.

**Architecture:** Keep the existing `LlmProfileRepository` boundary so API and game services do not need a new dependency shape. The repository will use SQLite only to resolve game/player profile ids, and JSON for profile CRUD. Database initialization will migrate old `llm_profiles` foreign keys away and drop the old table.

**Tech Stack:** Python 3.10 standard library `json`, `pathlib`, `sqlite3`, `unittest`; React + TypeScript + Vite; existing FastAPI adapter layer.

---

## File Structure

- Modify `backend/app/llm/profiles.py`: remove `max_tokens` from `LlmProfileInput` and `LlmProfile`.
- Modify `backend/app/llm/provider.py`: omit `max_tokens` from chat completion payload.
- Modify `backend/app/api/models.py`: remove `max_tokens` parsing from `LlmProfileRequest`.
- Modify `backend/app/storage/llm_profile_repository.py`: use JSON config file for LLM profile CRUD while retaining DB id resolution for games and players.
- Modify `backend/app/storage/schema.py`: remove `llm_profiles` table and foreign keys to it.
- Modify `backend/app/storage/database.py`: migrate old SQLite schemas that still reference `llm_profiles`, then drop the old profile table.
- Modify `backend/app/storage/game_repository.py`: stop checking profile ids against the removed `llm_profiles` table.
- Modify `tests/storage/test_llm_profile_repository.py`: verify file-backed CRUD and id resolution.
- Modify `tests/storage/test_database_schema.py`: verify no `llm_profiles` table and old schema migration.
- Modify `tests/api/test_api_models.py` and `tests/api/test_llm_profiles_api.py`: remove `max_tokens` requirements and ensure API stays masked.
- Modify `tests/llm/test_profiles.py` and `tests/ai/test_prompting_and_provider.py`: remove `max_tokens` from profile construction and provider payload expectations.
- Modify `frontend/src/api.ts` and `frontend/src/App.tsx`: remove `max_tokens` from types, defaults, edit state, and form fields.
- Modify `.gitignore`: ignore `config/`.
- Add `config.example/llm_profiles.json`: safe example without real secrets.
- Modify `README.md` and `docs/architecture.md`: document file-backed config and removed `max_tokens`.

## Task 1: Remove `max_tokens` From Core Profile Types

**Files:**
- Modify: `tests/llm/test_profiles.py`
- Modify: `tests/ai/test_prompting_and_provider.py`
- Modify: `backend/app/llm/profiles.py`
- Modify: `backend/app/llm/provider.py`

- [ ] **Step 1: Write failing tests**

In `tests/llm/test_profiles.py`, remove `max_tokens` from `LlmProfile` and `LlmProfileInput` construction and assert it is absent from public serialization:

```python
self.assertNotIn("max_tokens", public_dict)
```

In `tests/ai/test_prompting_and_provider.py`, remove `max_tokens` from `TEST_PROFILE` and assert the provider payload omits it:

```python
self.assertNotIn("max_tokens", captured["payload"])
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.llm.test_profiles tests.ai.test_prompting_and_provider -v
```

Expected: FAIL because `LlmProfileInput` and `LlmProfile` still require `max_tokens`, and provider still sends `max_tokens`.

- [ ] **Step 3: Implement minimal code**

Remove the `max_tokens` field and validation from `backend/app/llm/profiles.py`. Remove `"max_tokens": profile.max_tokens` from `backend/app/llm/provider.py`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.llm.test_profiles tests.ai.test_prompting_and_provider -v
```

Expected: PASS.

## Task 2: Make LLM Profiles File-Backed

**Files:**
- Modify: `tests/storage/test_llm_profile_repository.py`
- Modify: `backend/app/storage/llm_profile_repository.py`

- [ ] **Step 1: Write failing tests**

Add tests that construct the repository with a temporary config path:

```python
repository = LlmProfileRepository(
    connection,
    config_path=Path(tmpdir) / "config" / "llm_profiles.json",
)
```

Add assertions for these behaviors:

```python
self.assertEqual(repository.list_profiles(), [])
self.assertTrue((Path(tmpdir) / "config" / "llm_profiles.json").exists())
self.assertNotIn("test-key-1234567890abcdef", "".join(row[0] for row in connection.execute("select sql from sqlite_master").fetchall() if row[0]))
self.assertEqual(repository.get_profile("profile_1").api_key, "test-key-1234567890abcdef")
```

Add an update test where `profile_input(api_key="test-key-updated12345678")` replaces the key, and API tests in Task 4 cover blank-key preservation.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v
```

Expected: FAIL because `LlmProfileRepository` does not accept `config_path` and still writes profiles to SQLite.

- [ ] **Step 3: Implement minimal code**

Update `LlmProfileRepository.__init__`:

```python
def __init__(self, connection: sqlite3.Connection, config_path: str | Path | None = None):
    self._connection = connection
    self._config_path = Path(config_path) if config_path is not None else _default_config_path()
```

Implement `_default_config_path()` so `SOLOAVALON_LLM_CONFIG` wins, otherwise it returns `<repo>/config/llm_profiles.json`. Implement JSON load/save helpers using `{"profiles": [...]}` and atomic replace. Keep `resolve_profile_for_player()` SQL lookup for `players.llm_profile_id` and `games.default_llm_profile_id`, then call `get_profile(profile_id)`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v
```

Expected: PASS.

## Task 3: Remove SQLite Profile Table and Old Foreign Keys

**Files:**
- Modify: `tests/storage/test_database_schema.py`
- Modify: `backend/app/storage/schema.py`
- Modify: `backend/app/storage/database.py`
- Modify: `backend/app/storage/game_repository.py`

- [ ] **Step 1: Write failing tests**

Update schema test:

```python
self.assertNotIn("llm_profiles", table_names)
```

Add a migration test that creates an old schema with `llm_profiles`, `games.default_llm_profile_id` FK, `players.llm_profile_id` FK, and `ai_decisions.llm_profile_id` FK, inserts a game referencing `profile_1`, calls `initialize_database(connection)`, and then verifies:

```python
self.assertNotIn("llm_profiles", table_names)
self.assertEqual(connection.execute("select default_llm_profile_id from games where id = 'game_1'").fetchone()[0], "profile_1")
self.assertEqual([
    row["table"] for row in connection.execute("pragma foreign_key_list(games)").fetchall()
], [])
```

Update the game repository test so `set_player_llm_profile("game_1", "player_2", "missing_profile")` succeeds, because profile existence is no longer a SQLite concern.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_database_schema tests.storage.test_llm_profile_repository -v
```

Expected: FAIL because schema still creates `llm_profiles`, old foreign keys remain, and game repository still queries `llm_profiles`.

- [ ] **Step 3: Implement minimal code**

Remove `llm_profiles` table and profile-id foreign keys from `SCHEMA_SQL`. Add migration helpers in `database.py` that rebuild `games`, `players`, and `ai_decisions` when `pragma foreign_key_list(<table>)` contains `llm_profiles`, then `drop table if exists llm_profiles`. Remove the profile existence query in `GameRepository.set_player_llm_profile()`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_database_schema tests.storage.test_llm_profile_repository -v
```

Expected: PASS.

## Task 4: Update API Models and API Tests

**Files:**
- Modify: `tests/api/test_api_models.py`
- Modify: `tests/api/test_llm_profiles_api.py`
- Modify: `backend/app/api/models.py`
- Modify: `backend/app/api/llm_profiles.py`

- [ ] **Step 1: Write failing tests**

In `tests/api/test_api_models.py`, remove `"max_tokens"` from the payload and assert `LlmProfileRequest` has no `max_tokens` attribute:

```python
self.assertFalse(hasattr(request, "max_tokens"))
```

In `tests/api/test_llm_profiles_api.py`, pass a temp config path to the repository helper and remove `max_tokens` from create/update payloads. Keep:

```python
self.assertNotIn("api_key", created)
self.assertEqual(stored.api_key, "test-key-1234567890abcdef")
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.api.test_api_models tests.api.test_llm_profiles_api -v
```

Expected: FAIL because API request parsing still requires `max_tokens`.

- [ ] **Step 3: Implement minimal code**

Remove `max_tokens` from `LlmProfileRequest`, `from_payload()`, and `to_input()`. Keep blank key preservation in `LlmProfilesApi.update_profile()` unchanged.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.api.test_api_models tests.api.test_llm_profiles_api -v
```

Expected: PASS.

## Task 5: Update Frontend Model Form

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Make TypeScript changes**

Remove `max_tokens` from `LlmProfile` and `LlmProfileInput`. Remove `max_tokens` from `emptyProfile`, `editProfile()`, and the `Max Tokens` label block in `ProfileForm`.

- [ ] **Step 2: Verify frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS.

## Task 6: Add Ignored Config Example and Docs

**Files:**
- Modify: `.gitignore`
- Add: `config.example/llm_profiles.json`
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update ignore and example**

Add `config/` to `.gitignore`. Add `config.example/llm_profiles.json`:

```json
{
  "profiles": [
    {
      "id": "example",
      "name": "Example Provider",
      "base_url": "https://api.example.com/v1",
      "api_key": "replace-with-your-local-key",
      "model": "example-chat",
      "temperature": 0.7,
      "timeout": 30.0,
      "created_at": "2026-06-16T00:00:00Z",
      "updated_at": "2026-06-16T00:00:00Z"
    }
  ]
}
```

- [ ] **Step 2: Update docs**

Update README and architecture text so they state LLM profiles live in ignored `config/llm_profiles.json`, SQLite stores only profile ids in games/players/AI decisions, and `max_tokens` is not user-configurable.

## Task 7: Full Verification

**Files:**
- No code files.

- [ ] **Step 1: Run backend tests**

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

- [ ] **Step 3: Inspect git diff**

Run:

```powershell
git status --short
git diff --stat
```

Expected: only intended source, tests, docs, `.gitignore`, and config example changes.
