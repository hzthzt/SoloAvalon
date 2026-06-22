# GameService 重设计方案

本文档描述 `GameService` 的目标架构和游戏逐步推进机制。目标不是改变现有 HTTP API 或阿瓦隆规则，而是把当前集中在 `GameService` 里的推进、AI、提交和恢复逻辑拆成更深的模块，让每一步游戏推进具备原子提交、可恢复、可重试和可测试的性质。

当前实现状态：第一、二阶段已经落地到 `backend/app/services/game_flow.py`。`GameStateLoader`、`GameStepRunner`、`GameCommitter` 和 `GameAdvanceLoop` 已拆出；`GameCommitter` 已使用外层事务提交规则事件、AI 成功审计、AI 记忆快照、AI 失败审计和 `games` 摘要。`retry_paused_game()` 已在重试前从事件流刷新状态；人类动作入口为保持现有兼容性仍沿用内存态缓存，后续应在多实例一致性阶段切换为提交前重读。`games` 投影重建仍属于后续阶段。

## 设计目标

- `backend/app/game/rules.py` 继续作为唯一规则裁判。前端、API、AI 输出都只是动作来源。
- `GameService` 变成 API-facing 门面，保留现有公开方法语义。
- 每次游戏推进都通过单一提交点写入数据库，避免事件流、`games` 摘要和内存态分裂。
- 状态恢复只依赖玩家真相和规则事件；审计事件不改变 `GameState`。
- AI 失败只记录失败审计和 `error_paused` 状态，不写半步规则事件。
- 重试从最后一致规则状态继续，不依赖失败前的内存缓存。

## 建议模块

| 模块 | 职责 | 主要接口草案 |
| --- | --- | --- |
| `GameService` | API-facing 门面，负责权限入口、公开状态组装、房间详情聚合和调用推进模块。 | `create_game()`、`submit_human_action()`、`submit_human_ai_action()`、`retry_paused_game()`、`get_game_state()` |
| `GameStateLoader` | 从 `players` 和规则事件恢复 `GameState`，可选择校验 `games` 摘要投影。 | `load(game_id) -> LoadedGameState` |
| `GameAdvanceLoop` | 从当前状态循环推进，直到轮到真人、对局结束、AI 错误或安全步数上限。 | `advance_until_blocked(game_id, state) -> GameState` |
| `GameStepRunner` | 执行单个规则步，产出新状态和规则事件，不直接写数据库。 | `apply_human_action(game_id, state, action, human_player_id=...) -> StepResult`、`apply_player_action(state, player_id, action_type, payload) -> StepResult`、`finalize_vote(state) -> StepResult`、`finalize_quest(state) -> StepResult` |
| `AiDecisionRunner` | 构建上下文、调用模型、解析输出，返回可审计的 AI 决策结果或失败结果。 | `decide(game_id, state, player_id, phase, decision_type) -> AiDecisionOutcome` |
| `GameCommitter` | 事务提交点。当前集中写规则事件、AI 审计、AI 记忆、`games` 摘要和错误暂停状态；数据库提交成功后才更新内存态。 | `commit_step(game_id, state_before, step_result) -> GameState`、`commit_ai_error(game_id, ai_decision, audit_event) -> None` |

`GameService` 只组合这些模块，不再手写每个阶段的落库顺序。`GameStepRunner` 只理解规则和事件 payload，`GameCommitter` 只理解持久化一致性。

## 事件分类

规则事件会改变 `GameState`，必须可重放：

- `team_proposed`
- `speech`
- `vote_cast`
- `vote_result`
- `quest_action_submitted`
- `quest_result`
- `lady_of_lake_used`
- `assassination`

审计事件不改变 `GameState`，恢复时必须忽略：

- `game_created`
- `roles_assigned`
- `private_view_recorded`
- `ai_decision`

后续新增事件必须先选择分类。默认规则是：如果事件不参与 `_apply_replay_event()` 生成相同 `GameState`，它就是审计事件。

## 推进流程

### 人类动作

```text
GameService.submit_human_action
  -> GameStateLoader.load 读取最后一致状态
  -> 计算 next_human_action 并校验 action_type
  -> GameStepRunner.apply_human_action 产出 StepResult
  -> GameCommitter.commit_step 集中提交规则事件和 games 摘要
  -> GameAdvanceLoop.advance_until_blocked 推进后续 AI 动作
  -> GameService 返回公开 GameState
```

人类动作提交前必须重读状态，不能只相信内存缓存。`StepResult` 至少包含：

- `state_after`
- `rule_events`
- 可选的公开返回提示
- 可选的需要立即结算的派生事件，例如 `vote_result` 或 `quest_result`

### AI 动作

```text
GameAdvanceLoop
  -> 判断当前 phase 和 AI 行动者
  -> AiDecisionRunner.decide 生成动作建议和审计数据
  -> GameStateLoader.load 再次读取最新状态
  -> 校验行动者和 phase 仍匹配
  -> GameStepRunner.apply_player_action 应用玩家规则动作，或 finalize_* 处理系统结算
  -> GameCommitter.commit_step 集中提交规则事件和 games 摘要
```

AI 调用可能耗时较长，因此模型返回后应再次读取状态并校验未过期。当前服务层有锁，后续如果允许并发读写，这一步可以避免旧 AI 响应写入新阶段。

### AI 失败

```text
AiDecisionRunner.decide 抛出或返回失败
  -> GameCommitter.commit_ai_error
  -> 写入 ai_decisions 失败审计
  -> 追加 ai_decision 审计事件
  -> 设置 games.status = error_paused
  -> 不写任何规则事件
```

失败审计和 `error_paused` 应在同一事务中提交。失败前如果已有成功规则步，必须已经通过 `commit_step()` 完整提交；失败中的当前步不能留下半个规则事件。

### 重试

```text
GameService.retry_paused_game
  -> 确认 games.status == error_paused
  -> GameStateLoader.load 从规则事件恢复状态
  -> GameAdvanceLoop.advance_until_blocked 继续推进
```

重试不应该复用失败前的 `_states` 缓存。若未来保留缓存，也应在进入重试前丢弃对应 `game_id` 的缓存，确保从事件流恢复。

### 读取状态

```text
GameService.get_game_state
  -> 优先读取最后一致状态
  -> 组装公开视角和公开事件
  -> 不推进、不写事件、不改变 games 摘要
```

读取操作必须是纯查询。多次读取房间状态不能改变事件流或触发 AI。

## 提交和事务边界

目标形态下，`GameCommitter` 是唯一允许执行提交的模块。每个提交步应使用外层事务包住以下写入：

- 规则事件写入 `game_events`。
- AI 成功或失败审计写入 `ai_decisions`。
- AI 记忆快照写入 `ai_memory_snapshots`。
- `games` 摘要投影更新。
- 内存缓存更新。

第二阶段已让游戏推进路径使用 `autocommit=False` 的仓储实例，由 `GameCommitter` 控制 `commit` 和 `rollback`。默认仓储仍保留 `autocommit=True`，以兼容其他直接调用方。这样一次规则步要么完整落库，要么完全失败。

## 状态恢复策略

`GameStateLoader` 负责恢复和诊断：

- 从 `players` 重建初始 `GameState`。
- 只重放规则事件。
- 审计事件永远跳过。
- 遇到规则事件缺少必要私有 payload 时返回明确的数据损坏错误。
- 遇到历史坏尾部时，不长期静默截断；应输出诊断结果，标明最后成功 event_index 和第一个失败 event_index。

短期可以保留“读取有效前缀”的兼容策略，但应只用于已知历史坏数据，并在房间详情或日志中暴露诊断，避免未来真实损坏被隐藏。

## 迁移步骤

### 第一阶段：提取推进和提交模块，不改 API（已完成）

- 保留 `GameService` 公开方法和 API 响应结构。
- 新增 `GameStateLoader`，把 `_restore_state()` 和 `_apply_replay_event()` 搬入独立模块；恢复失败时返回最后成功和首个失败 event_index，`GameService` 将其转换为 `GameReplayError`，避免静默截断后继续分叉。
- 新增 `GameStepRunner`，把人类动作、AI 动作和系统结算的规则应用、事件 payload 构建集中起来。
- 新增 `GameAdvanceLoop`，从 `_auto_advance()` 拆出循环逻辑。
- 新增 `GameCommitter`，先调用现有仓储方法，保持行为兼容。
- `retry_paused_game()` 已从事件流重读状态；人类动作和 AI 动作仍从当前进程的 `GameState` 进入，行为保持兼容。

### 第二阶段：引入外层事务（已完成）

- 调整 `EventStore`、`GameRepository`、`AiDecisionRepository`、`AiMemoryRepository`，允许由调用方控制事务。
- `GameCommitter` 使用一个事务提交规则事件、AI 成功审计、记忆快照和 `games` 摘要。
- `GameCommitter.commit_ai_error()` 使用一个事务提交 AI 失败审计和 `error_paused` 状态。
- 已补回归测试，验证数据库不会出现事件、AI 审计、AI 记忆和摘要不一致。

### 第三阶段：明确 `games` 是投影缓存

- 文档和代码中明确 `game_events` 的规则事件是可重放事实源。
- `games.current_round`、`current_phase`、`winner`、`status` 是查询投影。
- 增加投影校验或重建工具，用于修复历史数据和诊断房间异常。

## 验收测试场景

- AI 在 `TEAM_PROPOSAL`、`SPEECH`、`VOTING`、`QUEST`、`LADY_OF_LAKE`、`ASSASSINATION` 任一阶段超时后，不产生当前步的规则事件。
- `retry_paused_game` 从最后一致状态继续推进，且不会重复写入已经提交过的规则事件。
- 多次调用 `get_game_state`、`get_room_detail` 和事件列表接口不会改变事件流。
- 状态恢复只重放规则事件；新增审计事件不会影响 `GameState`。
- 提交步中任一写入失败时，`game_events`、`ai_decisions`、`ai_memory_snapshots` 和 `games` 摘要不会部分提交。
- 历史坏尾部事件可以被诊断，错误位置明确，不再表现为普通 `InvalidActionError` 泄漏到 API。

## 兼容约束

- HTTP API 路径、请求 payload 和主要响应字段保持兼容。
- `game.rules` 的规则函数仍是最终裁判，不把规则判断下沉到前端或 AI。
- 公开事件过滤仍由 `event_visibility.py` 统一处理。
- 私有 payload 仍保留在数据库中，因为活跃对局恢复需要任务行动和湖女查验真相。
- 重构期间允许保留 `_states` 缓存，但缓存只能作为性能优化，不能作为权威状态源。
