# GameService 第二阶段实施计划

本文档记录第二阶段事务化改造的执行计划和验收口径。目标是在不改变 HTTP API 和规则行为的前提下，让一次游戏推进的数据库写入由 `GameCommitter` 统一提交。

## 完成状态

第二阶段已完成。仓储层保留默认 `autocommit=True` 兼容行为，同时支持 `autocommit=False`；`GameCommitter` 在游戏推进路径中使用事务型仓储实例，统一提交规则事件、AI 成功审计、AI 记忆、AI 失败审计和 `games` 摘要。`games` 投影重建、人类动作提交前强制重读事件流、更多服务方法的外层事务化留到后续阶段。

## 目标

- `EventStore`、`GameRepository`、`AiDecisionRepository`、`AiMemoryRepository` 支持关闭自动提交。
- `GameCommitter.commit_step()` 在一个事务中写入规则事件、AI 成功审计、AI 记忆快照和 `games` 摘要。
- `GameCommitter.commit_ai_error()` 在一个事务中写入 AI 失败审计和 `games.status = error_paused`。
- 任一写入失败时回滚本次提交，避免事件流、AI 审计、记忆快照和 `games` 摘要部分成功。
- 读取接口仍然不写数据库；现有 API payload 和响应字段保持兼容。

## 非目标

- 不重建 `games` 投影。
- 不改变 `game.rules` 的规则语义。
- 不把真人动作入口强制切换为每次提交前重读事件流；该项留到后续多实例一致性阶段。
- 不迁移 LLM profile 文件配置。

## 任务拆分

### 任务 1：仓储支持 `autocommit`

- 给 `EventStore`、`GameRepository`、`AiDecisionRepository`、`AiMemoryRepository` 增加 `autocommit: bool = True` 构造参数。
- 把内部 `self._connection.commit()` 改为 `_commit_if_needed()`。
- 默认行为不变，现有调用方无需修改。

验收：

- 默认仓储写入后仍立即持久化。
- `autocommit=False` 时，调用方可以 `rollback()` 撤销未提交写入。

### 任务 2：事务化 `GameCommitter.commit_step()`

- `GameCommitter` 内部使用 `autocommit=False` 的仓储实例。
- `commit_step()` 写入所有 `StepResult.rule_events`、可选 AI 决策、可选 AI 记忆快照和 `games` 摘要后统一 `commit()`。
- 写入期间任一异常触发 `rollback()`，并且不更新 `_states` 缓存。

验收：

- 模拟 `games` 摘要更新失败时，已插入的规则事件会回滚。
- 失败后内存态不前进。

### 任务 3：AI 成功路径进入 `StepResult`

- `StepResult` 承载可选 `AiDecisionInput` 和 `AiMemorySnapshotInput`。
- `_run_ai_turn()` 只返回 `AiTurnResult`，不直接写审计。
- AI 成功路径构造 `StepResult` 后由 `GameCommitter.commit_step()` 一次提交。

验收：

- AI 成功后仍能查询到 `ai_decisions`、`ai_memory_snapshots` 和对应规则事件。
- 如果规则事件写入失败，AI 审计和记忆快照也回滚。

### 任务 4：AI 失败路径进入 `GameCommitter`

- `_run_ai_turn()` 捕获 `AiDecisionError` 时调用 `GameCommitter.commit_ai_error()`。
- `commit_ai_error()` 写失败审计并设置 `games.status = error_paused`。
- 失败路径不写当前步规则事件。

验收：

- AI 超时后房间进入 `error_paused`。
- 事件流中没有当前失败动作对应的规则事件。
- 失败审计和 `error_paused` 要么一起写入，要么一起回滚。

### 任务 5：文档和回归验证

- 更新 `docs/backend-modules.md` 和 `docs/game-service-redesign.md`。
- 全量运行后端测试和编译检查。

验收：

- `python -m unittest discover -s tests` 全绿。
- `python -m compileall -q backend tests` 无错误。
