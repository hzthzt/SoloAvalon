# SoloAvalon 架构说明

本文档面向维护者，说明 SoloAvalon 当前本地 MVP 的模块边界、数据流、安全边界和扩展点。原始产品设计见 [2026-06-15-avalon-ai-design.md](./superpowers/specs/2026-06-15-avalon-ai-design.md)。

## 总览

SoloAvalon 是一个本地单机 Web 应用：FastAPI 后端负责规则裁判、AI 决策、持久化和 API；React 前端只渲染后端给出的真人视角状态并提交动作。

核心原则：

- 规则真相只存在于后端规则状态、SQLite 表和私有事件 payload 中。
- 前端永远不直接判断胜负、身份或隐藏行动。
- AI prompt 只能从过滤后的私有视角和公开事件历史构建。
- 事件日志是复盘和状态恢复的权威来源之一。
- 模型调用失败或输出非法时必须回落到确定性 fallback。

## 运行时分层

```text
frontend/
  React 界面 + api.ts
        |
        v
backend/app/api/
  FastAPI 路由 + payload 解析
        |
        v
backend/app/services/
  GameService 编排、事件可见性过滤、AI 自动推进
        |
        +--> backend/app/game/
        |     确定性阿瓦隆规则和私有视角
        |
        +--> backend/app/ai/ + prompting/ + llm/
        |     上下文构建、prompt 契约、模型调用、fallback
        |
        +--> backend/app/storage/
              SQLite 仓储、文件型模型配置和只追加事件日志
```

### 前端

主要文件：

- `frontend/src/App.tsx`：单页桌面，包括开局、游戏桌、模型配置、日志复盘。
- `frontend/src/api.ts`：后端 API 类型和请求封装。
- `frontend/src/styles.css`：应用布局和控件样式。

前端只消费 `GameState` 中的过滤字段。它可以显示真人玩家自己的身份、梅林或恶方可合法看到的信息、公开发言、公开投票结果和任务结果。前端不会拿到完整角色表或任务私有行动。

### API 层

主要文件：

- `backend/app/api/games.py`
- `backend/app/api/llm_profiles.py`
- `backend/app/api/models.py`

职责：

- 将 HTTP payload 标准化成服务层输入。
- 把服务层异常转换成 HTTP 400。
- 为公开事件列表应用可见性过滤。
- 暴露模型配置 CRUD 和连通性测试。

游戏 API：

- `POST /api/games`
- `GET /api/games`
- `GET /api/games/{game_id}`
- `POST /api/games/{game_id}/actions`
- `POST /api/games/{game_id}/ai-actions/human`
- `GET /api/games/{game_id}/events`
- `GET /api/games/{game_id}/export`
- `DELETE /api/games/{game_id}`

模型配置 API：

- `GET /api/llm-profiles`
- `POST /api/llm-profiles`
- `PUT /api/llm-profiles/{profile_id}`
- `DELETE /api/llm-profiles/{profile_id}`
- `POST /api/llm-profiles/{profile_id}/test`

### 服务层

主要文件：

- `backend/app/services/game_service.py`
- `backend/app/services/event_visibility.py`

`GameService` 是运行时编排器。它不实现规则细节，而是调用纯规则函数完成状态变更，然后同步写入 SQLite 摘要、事件日志、AI 决策和 AI 记忆快照。

服务层负责：

- 创建 5 人局并保存初始玩家、身份和私有视角事件。
- 接收真人动作并触发 AI 自动推进，直到下一个真人动作或对局结束。
- 让 AI 代替真人执行当前动作，方便自动测试完整对局。
- 从持久化玩家表和事件日志恢复活跃对局状态。
- 为 AI 决策提供公开事件历史。

`event_visibility.py` 是公开事件边界：

- 默认移除 `private_payload`。
- 未看到后续 `vote_result` 的 `vote_cast` 会隐藏具体票值。
- API 事件列表和 AI 公开历史共用这套过滤逻辑。

### 规则层

主要文件：

- `backend/app/game/models.py`
- `backend/app/game/rules.py`
- `backend/app/game/events.py`

规则层是确定性的纯领域层。当前实现标准 5 人局：

- 角色：Merlin、Assassin、Minion、Loyal Servant x2。
- 任务人数：2、3、2、3、3。
- 1 张失败票即任务失败。
- 3 次任务失败恶方胜利。
- 3 次任务成功进入刺杀，刺客刺中梅林则恶方胜利，否则善方胜利。

规则函数只接受 `GameState` 和动作参数，返回新的 `GameState`。非法动作抛出 `InvalidActionError`，例如非队长组队、重复投票、善方提交失败任务、非任务队员提交任务行动。

私有视角由 `private_view_for_player` 统一生成：

- 梅林看到恶方玩家，但只显示为 `unknown_evil`。
- 恶方互相知道恶方身份，但不暴露精确角色。
- 忠臣看不到隐藏身份。

### AI 与提示词层

主要文件：

- `backend/app/ai/context.py`
- `backend/app/ai/player.py`
- `backend/app/ai/strategy.py`
- `backend/app/prompting/templates.py`
- `backend/app/prompting/schemas.py`
- `backend/app/llm/provider.py`

AI 决策链路：

1. `GameService` 取当前状态和公开事件历史。
2. `AiPlayer` 调用 `ContextBuilder` 构建合法私有视角。
3. `PromptBuilder` 组合稳定前缀、阶段契约和动态私有后缀。
4. `LlmProvider` 调用 OpenAI-compatible Chat Completions。
5. `prompting.schemas` 解析并校验结构化 JSON 输出。
6. 输出非法或模型失败时使用 `FallbackStrategy`。
7. 服务层再次通过规则函数执行动作，形成最终裁判结果。
8. AI 决策摘要、上下文摘要、prompt 版本、解析结果和记忆快照写入 SQLite。

稳定前缀和动态后缀分离是为了提高兼容模型的 prompt cache 命中率。核心游戏逻辑不依赖任何厂商私有缓存能力。

### 存储层

主要文件：

- `backend/app/storage/schema.py`
- `backend/app/storage/database.py`
- `backend/app/storage/game_repository.py`
- `backend/app/storage/event_store.py`
- `backend/app/storage/llm_profile_repository.py`
- `backend/app/storage/ai_decision_repository.py`
- `backend/app/storage/ai_memory_repository.py`

SQLite 表：

- `games`：对局摘要和默认模型配置。
- `players`：席位、角色、阵营、每席模型覆盖。
- `game_events`：公开和私有事件流。
- `ai_decisions`：AI 决策摘要、模型输出摘要和 prompt/context 元数据。
- `ai_memory_snapshots`：每个 AI 玩家在不同阶段的私有记忆快照。

默认数据库文件是仓库根目录下的 `soloavalon.sqlite3`，可用 `SOLOAVALON_DB` 环境变量覆盖。

OpenAI-compatible 模型配置不写入 SQLite。`LlmProfileRepository` 默认从仓库根目录的 `config/llm_profiles.json` 读写明文配置，`config/` 已被 git 忽略；可用 `SOLOAVALON_LLM_CONFIG` 指向其他本地 JSON 文件。SQLite 中只保存对局默认配置和玩家覆盖配置的 profile id 字符串，AI 决策记录也只保存 profile id 和模型名。

## 关键数据流

### 创建对局

```text
POST /api/games
  -> CreateGameRequest
  -> GameService.create_game
  -> create_five_player_game
  -> GameRepository.save_new_game
  -> EventStore append game_created / roles_assigned / private_view_recorded
  -> _auto_advance until human turn
  -> human-filtered GameState
```

### 真人动作

```text
POST /api/games/{id}/actions
  -> validate expected human action
  -> execute game.rules function
  -> append public/private event
  -> _auto_advance AI seats
  -> persist summary and AI records
  -> human-filtered GameState
```

### AI 代替真人动作

```text
POST /api/games/{id}/ai-actions/human
  -> read current human action type
  -> run the same AiPlayer pipeline for player_1
  -> execute game.rules function
  -> append AI decision and game event
  -> _auto_advance remaining AI seats
```

这条路径只用于测试便利。它仍然使用真人玩家的合法私有视角，且动作仍由规则层校验。

### 复盘和导出

```text
GET /api/games/{id}/events
  -> EventStore.list_events
  -> public_event_dicts
  -> frontend event replay

GET /api/games/{id}/export
  -> EventStore.export_game_log
  -> optional include_private filtering
```

默认复盘和导出不包含 `private_payload`。调试时可以显式请求私有 payload，但前端目前只展示公开事件。

### 状态恢复

`GameService` 内存状态丢失时，会从 `players` 表重建基础 `GameState`，再按 `game_events` 顺序重放规则事件。`ai_decision` 和私有视角记录用于审计，不参与规则重放。

## 安全边界

- API Key 只在被 git 忽略的 `config/llm_profiles.json` 或 `SOLOAVALON_LLM_CONFIG` 指向的本地文件中保存，公开返回值只包含 `api_key_masked`。
- 更新模型配置时，空 `api_key` 表示保留旧密钥。
- 模型测试失败返回会脱敏密钥。
- 对局公开事件默认不包含 `private_payload`。
- 未结算投票不会公开具体票值。
- AI prompt 不直接接收完整游戏真相。
- SQLite、导出日志和 AI 决策记录默认不包含 API Key，也不包含私有 payload。

## 扩展点

当前规则层已经把状态、任务配置、身份和私有视角拆开。后续扩展建议：

- 新增 6-10 人局：扩展任务表、角色集合和创建函数。
- 新增身份：扩展 `Role`、`faction_for_role`、`private_view_for_player` 和输出标签。
- 新增湖中仙女：作为阶段或特殊机制插入服务层流程，不写进 5 人局硬编码。
- 新增难度配置：将 AI 策略参数和上下文预算挂到模型配置或单局配置。
- 新增调试台：读取 `ai_decisions` 和 `ai_memory_snapshots`，不要直接暴露 API Key。

## 测试策略

测试目录按层组织：

- `tests/game/`：规则流转、身份视角、胜负判定。
- `tests/storage/`：SQLite schema 和 repository。
- `tests/ai/`：上下文过滤、prompt、provider、fallback。
- `tests/services/`：服务编排、AI 自动推进、状态恢复。
- `tests/api/`：API payload、公开事件过滤、模型配置。

主要验证命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
```
