# Random Seat Player Alias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现五人局随机真人座位，并把所有公开玩家名匿名为 `玩家N`，同时只在玩家信息面板保留原名称。

**Architecture:** 后端领域模型新增 `original_name`，`name` 只表示公开匿名名。创建流程随机真人座位并按随机落座映射 AI 原名称和模型覆盖；AI 上下文、事件与日志继续只消费匿名 `name`。前端只在座位信息卡显示 `original_name`，其他流程继续使用匿名名。

**Tech Stack:** Python dataclasses/unittest/SQLite；FastAPI 风格 API 封装；React + TypeScript + Vite。

---

### Task 1: 规则层随机座位与匿名名

**Files:**
- Modify: `backend/app/game/models.py`
- Modify: `backend/app/game/rules.py`
- Test: `tests/game/test_rules_setup.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/game/test_rules_setup.py`:

```python
    def test_create_five_player_game_randomizes_human_seat_and_keeps_original_names(self):
        state = create_five_player_game(
            seed=20260619,
            human_name="张三",
            ai_names=["阿尔法", "贝塔", "伽马", "德尔塔"],
        )

        self.assertEqual([player.name for player in state.players], [
            "玩家1",
            "玩家2",
            "玩家3",
            "玩家4",
            "玩家5",
        ])
        human = next(player for player in state.players if player.is_human)
        self.assertNotEqual(human.id, "player_1")
        self.assertEqual(human.original_name, "张三")
        self.assertCountEqual(
            [player.original_name for player in state.players if not player.is_human],
            ["阿尔法", "贝塔", "伽马", "德尔塔"],
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.game.test_rules_setup.RulesSetupTests.test_create_five_player_game_randomizes_human_seat_and_keeps_original_names -v
```

Expected: FAIL because `create_five_player_game` does not accept `human_name` and `Player` has no `original_name`.

- [ ] **Step 3: Write minimal implementation**

Update `Player` in `backend/app/game/models.py`:

```python
@dataclass(frozen=True)
class Player:
    id: str
    seat_index: int
    name: str
    is_human: bool
    role: Role
    faction: Faction
    original_name: str | None = None
    llm_profile_id: str | None = None
```

Update `create_five_player_game` in `backend/app/game/rules.py`:

```python
def create_five_player_game(
    seed: int | None = None,
    human_seat_index: int | None = None,
    human_name: str = "真人玩家",
    ai_names: list[str] | None = None,
) -> GameState:
    rng = random.Random(seed)
    if human_seat_index is None:
        human_seat_index = rng.randrange(5)
    if human_seat_index < 0 or human_seat_index > 4:
        raise ValueError("human_seat_index must be between 0 and 4")

    roles = list(STANDARD_FIVE_PLAYER_ROLES)
    rng.shuffle(roles)
    configured_ai_names = list(ai_names or [])
    players = []
    ai_number = 1
    for seat_index, role in enumerate(roles):
        is_human = seat_index == human_seat_index
        if is_human:
            original_name = human_name.strip() or "真人玩家"
        else:
            default_ai_name = f"AI {ai_number}"
            original_name = (
                configured_ai_names[ai_number - 1].strip()
                if ai_number - 1 < len(configured_ai_names)
                and configured_ai_names[ai_number - 1].strip()
                else default_ai_name
            )
            ai_number += 1
        players.append(
            Player(
                id=f"player_{seat_index + 1}",
                seat_index=seat_index,
                name=f"玩家{seat_index + 1}",
                is_human=is_human,
                role=role,
                faction=faction_for_role(role),
                original_name=original_name,
            )
        )

    return GameState(players=tuple(players), missions=FIVE_PLAYER_MISSIONS)
```

- [ ] **Step 4: Run test to verify it passes**

Run the same command from Step 2. Expected: PASS.

---

### Task 2: 持久化 original_name

**Files:**
- Modify: `backend/app/storage/schema.py`
- Modify: `backend/app/storage/game_repository.py`
- Test: `tests/storage/test_game_repository.py`
- Test: `tests/storage/test_database_schema.py`

- [ ] **Step 1: Write the failing repository test**

Update `tests/storage/test_game_repository.py` expectation in `test_save_new_game_persists_summary_and_players`:

```python
self.assertEqual(players[0].name, "玩家1")
self.assertIsNotNone(players[0].original_name)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_game_repository.GameRepositoryTests.test_save_new_game_persists_summary_and_players -v
```

Expected: FAIL because `StoredPlayer` has no `original_name`.

- [ ] **Step 3: Write minimal persistence implementation**

Add `original_name: str | None` to `StoredPlayer`, add `original_name text` to the `players` table, include it in `insert into players`, and read `row["original_name"]`.

- [ ] **Step 4: Add schema compatibility test**

Update `tests/storage/test_database_schema.py` table SQL expectation for `players` to include:

```sql
original_name text,
```

- [ ] **Step 5: Run storage tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_game_repository tests.storage.test_database_schema -v
```

Expected: PASS.

---

### Task 3: 服务/API 创建参数、座位映射与 AI 隔离

**Files:**
- Modify: `backend/app/api/models.py`
- Modify: `backend/app/api/games.py`
- Modify: `backend/app/services/game_service.py`
- Test: `tests/api/test_games_api.py`
- Test: `tests/services/test_game_service.py`
- Test: `tests/ai/test_context_builder.py`

- [ ] **Step 1: Write failing service/API tests**

Add a service test that creates a game with `human_name="张三"` and AI names, then asserts public `name` values are `玩家N`, returned `original_name` contains original names, and the first AI prompt does not contain `张三` or configured AI names.

Add an API forwarding assertion to `tests/api/test_games_api.py`:

```python
api.create_game({"human_name": "张三", "ai_names": ["A"]})
self.assertEqual(service.create_kwargs["human_name"], "张三")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.services.test_game_service.GameServiceTests.test_create_game_returns_anonymous_names_and_keeps_original_names_out_of_prompt tests.api.test_games_api.GamesApiTests.test_games_api_does_not_forward_seed_to_service -v
```

Expected: FAIL because request/service do not support `human_name` and prompt may still use old names.

- [ ] **Step 3: Write minimal service/API implementation**

Add `human_name` to `CreateGameRequest`, forward it in `GamesApi.create_game`, pass it into `create_five_player_game`, remove direct AI rename mutation, and update `_apply_ai_configuration` so it only maps profile overrides to non-human players in seat order.

Update `_restore_state` and `_public_state` to include `original_name`.

- [ ] **Step 4: Add context isolation test**

In `tests/ai/test_context_builder.py`, build a state with `human_name="张三"` and AI names, then assert `context.dynamic_private_suffix` contains `玩家1` but not `张三` or AI original names.

- [ ] **Step 5: Run service/API/AI tests**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.services.test_game_service tests.api.test_games_api tests.ai.test_context_builder -v
```

Expected: PASS.

---

### Task 4: 前端创建表单与玩家信息面板

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.ts`

- [ ] **Step 1: Write failing frontend test**

Add or update a component test to assert a player card can show both anonymous `玩家N` and original name `张三`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd frontend
npm test -- App.test.ts
```

Expected: FAIL because `PlayerView` and `GameDesk` do not render `original_name`.

- [ ] **Step 3: Write minimal frontend implementation**

Add `original_name: string | null` to `PlayerView`, add `humanName` state and input in setup, pass `human_name` to `createGame`, render `player.original_name` as secondary text inside the seat card, and keep flow review/name maps based on `player.name`.

- [ ] **Step 4: Run frontend tests/build**

Run:

```powershell
cd frontend
npm test -- App.test.ts
npm run build
```

Expected: PASS.

---

### Task 5: Full verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run backend suite**

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
git diff --stat
git diff -- backend/app/game/rules.py backend/app/services/game_service.py backend/app/ai/context.py frontend/src/App.tsx
```

Expected: changed files match the feature scope and no unrelated edits are present.
