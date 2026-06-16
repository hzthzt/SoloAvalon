# LLM Profile Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend support for storing OpenAI-compatible model profiles, masking API keys, and resolving default or per-AI player profile bindings.

**Architecture:** Keep profile handling in pure Python storage/service modules so it can be tested without FastAPI or live model services. The SQLite table already exists; this slice adds safe data objects, CRUD repository methods, and a resolver that combines `games.default_llm_profile_id` with `players.llm_profile_id` overrides.

**Tech Stack:** Python 3.10, standard-library `sqlite3`, standard-library `unittest`, dataclasses, UTC ISO-8601 timestamps.

---

## File Structure

- `backend/app/llm/__init__.py`: package marker and exports.
- `backend/app/llm/profiles.py`: profile dataclasses, validation, and API key masking.
- `backend/app/storage/llm_profile_repository.py`: SQLite CRUD and binding resolution for LLM profiles.
- `backend/app/storage/__init__.py`: exports repository classes.
- `tests/llm/test_profiles.py`: unit tests for masking and validation.
- `tests/storage/test_llm_profile_repository.py`: repository CRUD and binding tests.

### Task 1: Profile Dataclasses and API Key Masking

**Files:**
- Create: `backend/app/llm/__init__.py`
- Create: `backend/app/llm/profiles.py`
- Create: `tests/llm/__init__.py`
- Test: `tests/llm/test_profiles.py`

- [ ] **Step 1: Write the failing masking tests**

Create `tests/llm/test_profiles.py`:

```python
import unittest

from backend.app.llm.profiles import LlmProfile, LlmProfileInput, mask_api_key


class LlmProfileTests(unittest.TestCase):
    def test_mask_api_key_hides_full_secret(self):
        self.assertEqual(mask_api_key("test-key-1234567890abcdef"), "test...cdef")
        self.assertEqual(mask_api_key("short"), "*****")
        self.assertEqual(mask_api_key(""), "")

    def test_public_dict_never_exposes_full_api_key(self):
        profile = LlmProfile(
            id="profile_1",
            name="DeepSeek",
            base_url="https://api.example.com/v1",
            api_key="test-key-1234567890abcdef",
            model="deepseek-chat",
            temperature=0.7,
            max_tokens=1024,
            timeout=30.0,
            created_at="2026-06-15T00:00:00Z",
            updated_at="2026-06-15T00:00:00Z",
        )

        public_dict = profile.to_public_dict()

        self.assertEqual(public_dict["api_key_masked"], "test...cdef")
        self.assertNotIn("api_key", public_dict)

    def test_input_validation_rejects_invalid_runtime_values(self):
        with self.assertRaises(ValueError):
            LlmProfileInput(
                name="Bad",
                base_url="https://api.example.com/v1",
                api_key="secret",
                model="model",
                temperature=-0.1,
                max_tokens=1024,
                timeout=30.0,
            )
        with self.assertRaises(ValueError):
            LlmProfileInput(
                name="Bad",
                base_url="https://api.example.com/v1",
                api_key="secret",
                model="model",
                temperature=0.5,
                max_tokens=0,
                timeout=30.0,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.llm.test_profiles -v`
Expected: FAIL because `backend.app.llm.profiles` does not exist.

- [ ] **Step 3: Implement profile dataclasses and masking**

Create `backend/app/llm/profiles.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


@dataclass(frozen=True)
class LlmProfileInput:
    name: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("profile name is required")
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.model.strip():
            raise ValueError("model is required")
        if self.temperature < 0 or self.temperature > 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass(frozen=True)
class LlmProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout: float
    created_at: str
    updated_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key_masked": mask_api_key(self.api_key),
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
```

Create `backend/app/llm/__init__.py`:

```python
from .profiles import LlmProfile, LlmProfileInput, mask_api_key

__all__ = ["LlmProfile", "LlmProfileInput", "mask_api_key"]
```

Create empty package marker `tests/llm/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.llm.test_profiles -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm tests/llm
git commit -m "feat: add llm profile value objects"
```

### Task 2: LLM Profile Repository CRUD

**Files:**
- Create: `backend/app/storage/llm_profile_repository.py`
- Modify: `backend/app/storage/__init__.py`
- Test: `tests/storage/test_llm_profile_repository.py`

- [ ] **Step 1: Write the failing CRUD tests**

Create `tests/storage/test_llm_profile_repository.py` with tests that create a temporary SQLite database, initialize schema, create a profile from `LlmProfileInput`, list it, retrieve it by id, update it, delete it, and assert public dictionaries never include `api_key`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v`
Expected: FAIL because `LlmProfileRepository` does not exist.

- [ ] **Step 3: Implement CRUD repository**

Create `backend/app/storage/llm_profile_repository.py` with a `LlmProfileRepository` class exposing:

```python
create_profile(profile_id: str, profile_input: LlmProfileInput) -> LlmProfile
get_profile(profile_id: str) -> LlmProfile | None
list_profiles() -> list[LlmProfile]
update_profile(profile_id: str, profile_input: LlmProfileInput) -> LlmProfile
delete_profile(profile_id: str) -> None
```

Use the existing `llm_profiles` table, store the secret in `api_key_encrypted_or_masked` for now, and map it back into the internal `LlmProfile.api_key`. Keep external serialization safe through `to_public_dict()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage/llm_profile_repository.py backend/app/storage/__init__.py tests/storage/test_llm_profile_repository.py
git commit -m "feat: manage llm profiles in sqlite"
```

### Task 3: Default and Per-Player Profile Resolution

**Files:**
- Modify: `backend/app/storage/llm_profile_repository.py`
- Modify: `backend/app/storage/game_repository.py`
- Test: `tests/storage/test_llm_profile_repository.py`

- [ ] **Step 1: Write failing binding resolution tests**

Extend `tests/storage/test_llm_profile_repository.py` with tests that:

- create default and override profiles,
- save a game with `default_llm_profile_id`,
- update one AI player's `llm_profile_id` override,
- assert `resolve_profile_for_player(game_id, player_id)` returns the override for that player and the default for another AI player,
- assert a missing default or missing override id raises `ValueError`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v`
Expected: FAIL because binding resolution and player profile update are not implemented.

- [ ] **Step 3: Implement binding resolution**

Add to `GameRepository`:

```python
def set_player_llm_profile(self, game_id: str, player_id: str, llm_profile_id: str | None) -> None:
    ...
```

Add to `LlmProfileRepository`:

```python
def resolve_profile_for_player(self, game_id: str, player_id: str) -> LlmProfile:
    ...
```

Resolution should prefer `players.llm_profile_id`; if empty, use `games.default_llm_profile_id`; if neither exists or the referenced profile is missing, raise `ValueError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage tests/storage/test_llm_profile_repository.py
git commit -m "feat: resolve ai player llm profile bindings"
```

### Task 4: Full Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document profile management scope**

Update `README.md` with:

```markdown
LLM profile management currently covers backend storage, API key masking for public output, and default/per-player binding resolution. Live provider calls and FastAPI routes are planned later.
```

- [ ] **Step 2: Run focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.llm.test_profiles tests.storage.test_llm_profile_repository -v`
Expected: PASS.

- [ ] **Step 3: Run full backend tests**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document llm profile management scope"
```

## Self-Review

- Spec coverage: Covers model profile fields, API key masking, default profile use, per-player overrides, and safe public serialization. It does not cover FastAPI routes, UI, or live model health checks.
- Placeholder scan: The plan uses concrete task names, file paths, commands, and implementation details without unresolved placeholder markers.
- Type consistency: `LlmProfile`, `LlmProfileInput`, `LlmProfileRepository`, and `resolve_profile_for_player` are named consistently across tests and implementation steps.
