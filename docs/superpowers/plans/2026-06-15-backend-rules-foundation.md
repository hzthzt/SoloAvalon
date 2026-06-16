# Backend Rules Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first backend slice: a deterministic Avalon rules core that can create a 5-player game, enforce phase actions, filter private views, resolve quests, and handle assassination.

**Architecture:** Keep the rules engine independent from FastAPI, SQLite, and LLM code so it can be tested without external services. The initial package exposes pure domain objects and service functions under `backend/app/game/`, while `backend/app/main.py` only proves the future API host can import.

**Tech Stack:** Python 3.10, standard-library `unittest` for the first local verification pass, pytest declared for later use once local pip networking is available, FastAPI dependency declared for later API work, dataclasses/enums for the rules core.

---

### Task 1: Python Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/game/__init__.py`

- [ ] **Step 1: Write the failing import test**

Create `tests/test_project_skeleton.py`:

```python
def test_backend_app_imports():
    from backend.app.main import app

    assert app.title == "SoloAvalon"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_project_skeleton -v`
Expected: FAIL because `backend.app.main` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `pyproject.toml`:

```toml
[project]
name = "soloavalon"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi>=0.111,<1.0",
  "uvicorn[standard]>=0.30,<1.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2,<9.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Create `backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="SoloAvalon")
```

Create empty package marker files at `backend/app/__init__.py` and `backend/app/game/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_project_skeleton -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml backend/app tests/test_project_skeleton.py
git commit -m "chore: scaffold backend package"
```

### Task 2: Roles, Game Creation, and Private Knowledge

**Files:**
- Create: `backend/app/game/models.py`
- Create: `backend/app/game/rules.py`
- Test: `tests/game/test_rules_setup.py`

- [ ] **Step 1: Write failing tests for setup and role knowledge**

Create `tests/game/test_rules_setup.py` with tests that create a seeded game, assert 5 seats contain Merlin, Assassin, Minion, and two Loyal Servants, assert the human seat is marked human, assert Merlin sees both evil players, evil players see each other, and Loyal Servants see no hidden roles.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_rules_setup -v`
Expected: FAIL because `backend.app.game.rules` does not exist.

- [ ] **Step 3: Implement setup models and view filtering**

Add enums `Role`, `Faction`, `Phase`, `MissionAction`, `Vote`, dataclasses `Player`, `MissionConfig`, `GameState`, and pure functions `create_five_player_game(seed: int | None, human_seat_index: int = 0)` and `private_view_for_player(state, viewer_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_rules_setup -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/game tests/game/test_rules_setup.py
git commit -m "feat: create five player game setup"
```

### Task 3: Team Proposal, Speaking, and Voting Flow

**Files:**
- Modify: `backend/app/game/models.py`
- Modify: `backend/app/game/rules.py`
- Test: `tests/game/test_team_vote_flow.py`

- [ ] **Step 1: Write failing phase-flow tests**

Create tests that assert only the leader can propose a team, the required first quest team size is 2, speeches advance in fixed order from the leader, majority approve advances to quest action, rejected votes rotate leader and increment the failed vote count, and the fifth rejected vote enables a forced team proposal that bypasses voting.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_team_vote_flow -v`
Expected: FAIL because team proposal and vote functions do not exist.

- [ ] **Step 3: Implement minimal flow functions**

Add `propose_team`, `record_speech`, `cast_vote`, `finalize_vote`, and `force_team_after_failed_votes`, returning updated game state and raising `InvalidActionError` for illegal actions.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_team_vote_flow -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/game tests/game/test_team_vote_flow.py
git commit -m "feat: enforce team proposal and voting flow"
```

### Task 4: Quest Resolution and Assassination

**Files:**
- Modify: `backend/app/game/models.py`
- Modify: `backend/app/game/rules.py`
- Test: `tests/game/test_quest_and_endgame.py`

- [ ] **Step 1: Write failing quest and endgame tests**

Create tests that assert only quest members can submit quest actions, good players cannot submit fail, one fail card fails a 5-player quest, three failed quests end with evil victory, three successful quests enter assassination, assassin killing Merlin gives evil victory, and assassin missing Merlin gives good victory.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_quest_and_endgame -v`
Expected: FAIL because quest and assassination functions do not exist.

- [ ] **Step 3: Implement quest and endgame functions**

Add `submit_quest_action`, `finalize_quest`, and `assassinate`, keeping all validation in the backend rules layer and recording only aggregate quest outcome on public state.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.game.test_quest_and_endgame -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/game tests/game/test_quest_and_endgame.py
git commit -m "feat: resolve quests and assassination"
```

### Task 5: Full Slice Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the backend rule slice**

Update `README.md` with setup and test commands:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document backend rule verification"
```
