# SoloAvalon 模块与逻辑架构

核对日期：2026-10-17。本文面向维护者和后续协作 AI，记录当前实现。后端细节见[后端模块与运行机制](./backend-modules.md)，尚未实现的目标与迁移步骤见[后端重设计方案](./game-service-redesign.md)，领域用语见[领域术语](../CONTEXT.md)。[最初产品设计](./superpowers/specs/2026-06-15-avalon-ai-design.md)属于历史规格，不作为当前能力清单。

## 1. 项目定位

SoloAvalon 是一个本地单人《阿瓦隆》Web 应用。当前运行形态是：

- FastAPI 后端负责规则裁判、状态推进、AI 决策调用、事件日志和持久化。
- React 前端负责 5-10 人开局配置、真人动作输入、对局桌面、模型配置和房间复盘展示。
- SQLite 保存对局、玩家、事件和 AI 审计记录。
- OpenAI-compatible 模型配置保存到本地 JSON 文件，默认路径是被 git 忽略的 `config/llm_profiles.json`。

设计边界：

- 后端是唯一规则裁判；前端不自行推断隐藏身份、胜负或任务结果。
- 公开 API 与 AI prompt 只能使用过滤后的公开事件和合法私有视角。
- 事件流是复盘、导出和状态恢复的重要来源。
- 真实 API Key 不进入 SQLite，不进入 git 跟踪文件。

这些是维护约束，不代表所有接口都已经满足。当前对局列表、AI 审计事件和进行中私有审计访问仍有可见性缺口；模型测试和中断续局也有已知问题，见[后端已知缺口](./backend-modules.md#已知缺口)。当前没有确定性 AI fallback。

## 2. 总体运行架构

```mermaid
flowchart TD
    Browser["浏览器 / React 单页应用"] --> Api["FastAPI 路由层\nbackend/app/api"]
    Api --> Service["GameService 编排层\nbackend/app/services"]
    Service --> Rules["确定性规则层\nbackend/app/game"]
    Service --> Ai["AI 决策链路\nbackend/app/ai + prompting + llm"]
    Service --> Storage["SQLite 与文件仓储\nbackend/app/storage"]
    Api --> ProfileApi["模型配置 CRUD / 连通性测试"]
    ProfileApi --> Storage
    ProfileApi --> Provider
    Ai --> Provider["OpenAI-compatible Chat Completions"]
    Storage --> Sqlite["soloavalon.sqlite3"]
    Storage --> Profiles["config/llm_profiles.json"]
```

对局请求由 API 调用服务层，服务层协调规则、AI 和存储；模型配置 API 则直接调用配置仓储与 Provider。规则层不依赖 API、前端或数据库，因此可以用单元测试直接验证。SSE 是后端向前端输出增量事件的通道，不负责推进规则。

## 3. 顶层目录职责

| 路径 | 职责 |
| --- | --- |
| `backend/app/main.py` | 应用启动入口，初始化 SQLite、`GameService`、模型配置仓储和 FastAPI 路由。 |
| `backend/app/api/` | HTTP 适配层，解析请求 payload，暴露对局和模型配置 API。 |
| `backend/app/services/` | 运行时编排层，连接规则层、AI 决策、事件日志和持久化。 |
| `backend/app/game/` | 阿瓦隆领域模型、确定性规则函数、私有事件 payload 构建。 |
| `backend/app/ai/` | AI 视角上下文构建、决策执行器和决策数据结构。 |
| `backend/app/prompting/` | prompt 配置加载、消息模板、模型输出解析与校验。 |
| `backend/app/llm/` | OpenAI-compatible provider 和模型配置类型。 |
| `backend/app/storage/` | SQLite schema、对局仓储、事件仓储、AI 审计仓储和文件型模型配置仓储。 |
| `frontend/src/` | React 单页 UI、API client、对局复盘事件转换和样式。 |
| `config.example/` | 示例模型配置和 prompt 模板配置，不包含真实密钥。 |
| `scripts/`、`start.bat` | Windows 本地一键启动脚本。 |
| `tests/` | 按后端层级组织的 unittest 测试。 |
| `docs/` | 架构说明、prompt 说明、历史规格和执行计划。 |

## 4. 后端模块

### 4.1 应用入口：`backend/app/main.py`

入口模块完成运行时装配：

- 从 `SOLOAVALON_DB` 读取数据库路径，默认使用仓库根目录 `soloavalon.sqlite3`。
- 调用 `connect_sqlite()` 和 `initialize_database()` 初始化连接与 schema。
- 创建全局 `GameService` 和 `LlmProfileRepository`。
- 创建 FastAPI 应用，配置允许本地 Vite 源的 CORS。
- 挂载 `games` 与 `llm-profiles` 两组路由。

该文件只做装配，不承载业务规则。装配发生在模块导入时，目前使用全局服务和共享连接，尚未通过应用生命周期统一启动、恢复和关闭推进任务。

### 4.2 API 层：`backend/app/api/`

API 层由四个功能文件组成：

- `models.py`：请求模型与 payload 规范化，包括 `CreateGameRequest`、`HumanActionRequest`、`LlmProfileRequest`。
- `games.py`：对局 API，提供创建、查询、动作提交、AI 代打、错误重试、事件与 SSE、房间详情、归档、导出和删除。
- `llm_profiles.py`：模型配置 API，提供配置 CRUD 和连通性测试。
- `errors.py`：将异常整理为消息、类型和 traceback。

请求模型使用 dataclass 和手动 `from_payload()`，路由请求体与响应主要是字典。API 不直接修改规则状态；对局经过 `GameService`，配置操作直接使用仓储。参数错误映射为 HTTP 400，AI 超时为 504，其他 AI 决策错误为 502，未预期异常为 500。

游戏 API：

- `POST /api/games`
- `GET /api/games`
- `GET /api/games/{game_id}`
- `POST /api/games/{game_id}/actions`
- `POST /api/games/{game_id}/ai-actions/human`
- `POST /api/games/{game_id}/retry`
- `GET /api/games/{game_id}/events`
- `GET /api/games/{game_id}/events/stream`
- `GET /api/games/{game_id}/export`
- `GET /api/games/{game_id}/room`
- `POST /api/games/{game_id}/archive`
- `DELETE /api/games/{game_id}`

模型配置 API：

- `GET /api/llm-profiles`
- `POST /api/llm-profiles`
- `PUT /api/llm-profiles/{profile_id}`
- `DELETE /api/llm-profiles/{profile_id}`
- `POST /api/llm-profiles/{profile_id}/test`

### 4.3 服务层：`backend/app/services/`

`GameService` 是对局业务门面，维护内存态 `GameState` 缓存，协调规则、AI、查询组装和持久化。单步执行、提交、恢复和循环已经拆入 `game_flow.py`：

| 组件 | 职责 |
| --- | --- |
| `GameStepRunner` | 调用规则函数，构造待提交的新状态与规则事件。 |
| `GameCommitter` | 原子提交一次规则步及 AI 审计，成功后更新缓存并通知订阅。 |
| `GameStateLoader` | 从建局元数据、玩家真相和规则历史重建状态，保留错误位置。 |
| `GameAdvanceLoop` | 持续选择 AI 行动或系统结算，直到真人、结束、错误或步数上限。 |

主要职责：

- 创建新对局，保存 `games`、`players` 初始记录。
- 记录 `game_created`、`roles_assigned`、`private_view_recorded` 等初始事件。
- 接收真人动作，调用规则层函数推进状态。
- 自动推进 AI 席位，直到轮到真人、对局结束或达到安全步数上限。
- 为 AI 决策读取公开事件历史，并保存 AI 决策与记忆快照。
- 在内存状态缺失时，从玩家表和事件流重放恢复 `GameState`。
- 导出事件日志、列出对局、归档与删除对局，聚合房间详情和用量。

`PendingEvent` 和 `StepResult` 是未提交结果的数据载体。`GameCommitter` 统一保存规则事件、AI 决策、记忆、审计事件和摘要；建局的多次写入尚未采用同一外层事务。写入口使用一把覆盖模型调用的全局 `RLock`，不同房间也会串行等待。

`event_visibility.py` 是公开事件边界：

- 默认移除 `private_payload`。
- 对尚未出现 `vote_result` 的 `vote_cast` 隐藏具体票值。
- API 事件列表和 AI 公开历史共用这套过滤逻辑。

这套过滤不会自动识别公开 payload 中误放的私有信息。`event_notifier.py` 只负责提交后的进程内唤醒，SSE 的实际事件从数据库读取；它不是持久化任务队列。

### 4.4 规则层：`backend/app/game/`

规则层是确定性领域核心，主要文件：

- `models.py`：定义 `Role`、`Faction`、`Phase`、`Vote`、`MissionAction`、`GameOption`、`Player`、`MissionConfig`、`LadyOfLakeInspection`、`GameState`、`PrivateView`。
- `rules.py`：实现身份阵营映射、建局、私有视角、组队、发言、投票、任务、湖女、刺杀和胜负结算。
- `events.py`：构建开局、身份分配、私有视角记录的公开/私有事件 payload。

运行中的动作事件主要由 `GameStepRunner` 构造，`game/events.py` 不是所有事件的统一定义中心。

当前规则常量包含：

- 5-10 人任务配置 `STANDARD_MISSION_CONFIGS`。
- 5-10 人阵营人数配置 `STANDARD_FACTION_COUNTS`。
- 5-10 人基础身份配置 `STANDARD_ROLE_SETUPS`：
  - 5 人：梅林、派西维尔、忠臣；莫甘娜、刺客。
  - 6 人：梅林、派西维尔、2 名忠臣；莫甘娜、刺客。
  - 7 人：梅林、派西维尔、2 名忠臣；莫甘娜、刺客、奥伯伦。
  - 8 人：梅林、派西维尔、3 名忠臣；莫甘娜、刺客、爪牙。
  - 9 人：梅林、派西维尔、4 名忠臣；莫甘娜、刺客、莫德雷德。
  - 10 人：梅林、派西维尔、4 名忠臣；莫甘娜、刺客、莫德雷德、奥伯伦。
- `create_game()` 支持 5-10 人标准局，`create_five_player_game()` 是兼容封装。
- 9-10 人可开启 Tristan / Isolde，规则层会用这对身份替换两个忠臣。
- 8-10 人可开启湖中仙女，湖女作为完整规则阶段处理。

阶段流转：

```mermaid
stateDiagram-v2
    [*] --> TEAM_PROPOSAL
    TEAM_PROPOSAL --> SPEECH: 正常组队
    TEAM_PROPOSAL --> QUEST: 第 5 次拒绝后强制组队
    SPEECH --> VOTING: 所有人发言完成
    VOTING --> QUEST: 多数赞成
    VOTING --> TEAM_PROPOSAL: 未过半赞成
    QUEST --> TEAM_PROPOSAL: 未达到 3 成功/3 失败
    QUEST --> LADY_OF_LAKE: 第 2/3/4 次任务后触发湖女
    LADY_OF_LAKE --> TEAM_PROPOSAL: 查验并转交湖女
    QUEST --> ASSASSINATION: 3 次任务成功
    QUEST --> COMPLETE: 3 次任务失败
    ASSASSINATION --> COMPLETE
```

关键规则约束：

- 只有当前队长能提车队。
- 车队人数必须匹配当前任务配置。
- 发言按队长开始的固定座位顺序进行。
- 投票必须全员完成后才能结算，多数赞成才通过。
- 第五次连续组队失败后触发强制车队，绕过发言和投票直接进入任务。
- 只有任务队员能提交任务行动。
- 善方不能提交失败任务。
- 任务失败牌数量达到当前任务阈值时任务失败。
- 3 次任务失败恶方胜利；3 次任务成功进入刺杀。
- 只有刺客能刺杀，刺中梅林则恶方胜利，否则善方胜利。
- 启用湖中仙女时，第 2、3、4 次任务结算且未进入刺杀/结束时进入 `LADY_OF_LAKE`；持有者只能查看一名从未持有过湖女的玩家阵营，结果只进入持有者私有视角，随后湖女转交给目标。

私有视角由 `private_view_for_player()` 统一生成：

- 玩家总能看到自己的精确身份。
- 梅林看到除 Mordred 外的恶方，但只标记为 `unknown_evil`。
- Percival 看到 Merlin 和 Morgana 候选，但只标记为 `unknown_merlin`。
- 非 Oberon 恶方能看到其他非 Oberon 恶方，但不暴露精确角色。
- Tristan 和 Isolde 能互相识别。
- 湖女持有者能看到自己通过湖女查验得到的目标阵营。
- 普通忠臣看不到隐藏身份。

### 4.5 AI 与 prompt 链路：`backend/app/ai/`、`backend/app/prompting/`、`backend/app/llm/`

AI 决策链路：

```mermaid
sequenceDiagram
    participant S as GameService
    participant A as AiPlayer
    participant C as ContextBuilder
    participant P as PromptBuilder
    participant L as LlmProvider
    participant V as schemas
    participant R as rules.py
    participant DB as SQLite

    S->>A: 当前 GameState + 玩家 ID + 公开事件 + profile
    A->>C: 构建当前玩家上下文
    C-->>A: AiContext
    A->>P: 根据阶段构建 messages
    P-->>A: system/user messages
    A->>L: chat_completion(profile, messages)
    L-->>A: LlmCompletionResult（文本与用量）
    A->>V: 解析并校验 JSON 或可接受文本
    V-->>A: 决策对象
    A-->>S: AiTurnResult
    S->>R: 经 GameStepRunner 执行决策
    S->>DB: 经 GameCommitter 提交事件、决策与记忆
```

模块职责：

- `ai/context.py`：构建 `AiContext`。内容包括稳定前缀、动态私有后缀、合法私有视角、公开状态、近期公开事件、合法动作和上下文摘要。
- `ai/player.py`：执行一次 AI 决策，调用 provider，解析输出，返回 `AiTurnResult`；模型调用或输出非法时抛出 `AiDecisionError`。
- `ai/strategy.py`：定义 AI 决策数据结构，如组队、发言、投票、任务行动、刺杀和湖女；不实现独立策略引擎。
- `prompting/config.py`：从 `SOLOAVALON_PROMPT_CONFIG` 或 `config.example/prompt_templates.json` 加载 prompt 文案和契约。
- `prompting/templates.py`：把 `AiContext` 转换成 Chat Completions messages。
- `prompting/schemas.py`：解析模型输出，校验队伍、投票、任务行动、刺杀目标等结构。
- `llm/provider.py`：用标准库 `urllib.request` 调用 OpenAI-compatible `/chat/completions`。
- `llm/profiles.py`：定义模型配置类型和 API Key 脱敏函数。

上下文分层：

- `stable_prefix`：系统规则、人数、阵营数、任务配置、身份配置和可选机制说明，生成 `stable_prefix_hash` 便于审计和缓存策略记录。
- `dynamic_private_suffix`：当前玩家私有视角、公开状态、近期事件和合法动作的结构化快照；不直接作为 JSON 发给模型。
- `recent_public_events`：来自 `event_visibility.public_event_dicts()`，删除私有 payload 后再由模板渲染活动日志。字段误分类的缺口见后端说明。

最终消息为稳定系统前缀，以及玩家视角、动作格式、活动日志、本次行动四条 user 消息。Provider 返回 `LlmCompletionResult`，包含文本与 Token、缓存用量。Provider 对捕获到的超时和空回答重试，`AiPlayer` 还对解析失败重试；二者目前共用 `timeout_retries`。

当前 AI 决策失败会在同一事务中保存失败审计与 `error_paused`，再抛出异常；没有确定性 fallback。记忆快照目前只用于留档，未读入后续决策上下文；稳定前缀哈希与用量记录也不代表后端已有缓存服务。

## 5. 存储模块：`backend/app/storage/`

存储层分为 SQLite 仓储和文件型配置仓储。

### 5.1 SQLite schema

`schema.py` 定义的表：

| 表 | 内容 |
| --- | --- |
| `games` | 建局元数据、可选项、对局摘要、运行状态、归档信息和默认模型配置 id。 |
| `players` | 对局内玩家座位、名称、是否真人、真实身份、阵营、玩家级模型配置覆盖。 |
| `game_events` | 只追加事件流，拆分 `public_payload` 和 `private_payload`。 |
| `ai_decisions` | AI 决策审计，包括输入摘要、输出、模型名、版本、校验状态和 Token 用量；完整 Prompt 保存在对应事件的私有 payload。 |
| `ai_memory_snapshots` | AI 私有记忆留档，目前主要是决策观察摘要；尚未形成后续决策的记忆来源。 |

`database.py` 负责：

- 创建 SQLite 连接并启用外键。
- 初始化 schema。
- 迁移旧版本中指向 `llm_profiles` SQLite 表的外键。
- 删除旧的 `llm_profiles` 表；当前模型配置不再存入 SQLite。

### 5.2 仓储职责

| 文件 | 职责 |
| --- | --- |
| `game_repository.py` | 保存新对局、列出摘要、读取玩家、更新状态、归档、删除、设置玩家模型配置。 |
| `event_store.py` | 追加事件、按序列出事件、导出对局日志。 |
| `ai_decision_repository.py` | 保存和读取 AI 决策审计记录。 |
| `ai_memory_repository.py` | 保存和读取 AI 记忆快照，并校验记忆 payload 结构。 |
| `llm_profile_repository.py` | 读写 `config/llm_profiles.json`，并按玩家覆盖优先于对局默认配置解析模型。 |

当前文件配置仓储还查询 SQLite 以解析配置 ID，尚未完全隔离文件 I/O 与对局配置选择。普通仓储默认自动提交，推进路径使用 `autocommit=False` 实例由 `GameCommitter` 控制事务。更完整的状态与事务说明见[后端模块文档](./backend-modules.md#状态与事务边界)。

模型配置文件默认路径：

- `SOLOAVALON_LLM_CONFIG` 指定时使用该路径。
- 否则使用仓库根目录 `config/llm_profiles.json`。
- 模型推理参数使用 `reasoning_effort`；旧 `temperature` 字段只做兼容读取并按 `medium` 处理，不再写入或发送给模型。
- `timeout_retries` 默认为 5，同时用于 Provider 的部分失败重试与 AI 输出解析重试。

prompt 配置文件默认路径：

- `SOLOAVALON_PROMPT_CONFIG` 指定时使用该路径。
- 否则使用 `config.example/prompt_templates.json`。

## 6. 前端模块：`frontend/src/`

前端是 React + TypeScript + Vite 单页应用，主要文件：

| 文件 | 职责 |
| --- | --- |
| `App.tsx` | 应用主组件，包含对局、模型、房间三个标签页和主要交互状态。 |
| `api.ts` | 后端 API 类型定义与 fetch 封装。 |
| `flowReview.ts` | 将事件流转换为轮次复盘、广播记录和信息流展示数据。 |
| `apiKey.ts` | 新输入 API Key 的前端展示脱敏辅助函数。 |
| `styles.css` | 桌面、面板、座位、动作区、模型表单、日志复盘样式。 |
| `main.tsx` | React 挂载入口。 |

三个主要视图：

- 对局：创建 5-10 人局、选择默认模型和 AI 覆盖模型、配置可选项、显示座位与任务轨道、提交真人动作、触发 AI 代打。
- 模型：创建、编辑、删除和测试 OpenAI-compatible 模型配置。
- 房间：查看归档房间、加载详情、按轮次复盘、导出 JSON；完成态展示完整 AI 决策。

前端数据原则：

- `GameState` 中只使用后端返回的 `visible_role`、`human_role`、`known_evil_player_ids`、`lady_of_lake_known_factions` 等过滤字段。
- 前端根据 `next_human_action` 决定显示哪种动作控件。
- `flowReview.ts` 只把事件转为展示结构，不裁判规则。
- 实时对局使用公开事件；完成态加载房间详情以展示完整决策。房间页直接请求 `/room`，并不是通过 `include_private=true` 才取得全部私有事件。

Vite 配置：

- 开发服务器端口默认 `5173`。
- `/api` 默认代理到 `http://127.0.0.1:8000`，支持 `SOLOAVALON_BACKEND_URL`。一键启动脚本可选择其他端口，并同步设置代理目标和前端 CORS 源。

## 7. 详细对局流程

### 7.1 创建对局

```text
POST /api/games
  -> CreateGameRequest.from_payload
  -> GameService.create_game(auto_advance=False)
  -> create_game
  -> 应用 AI 名称和模型覆盖配置
  -> GameRepository.save_new_game
  -> EventStore 记录 game_created / roles_assigned / private_view_recorded
  -> 返回初始真人过滤视角 GameState
响应后的 BackgroundTasks
  -> GameService.advance_game
  -> GameAdvanceLoop 推进 AI，提交后唤醒 SSE
```

创建对局的关键结果有三类：

- 规则状态：`GameState` 进入 `TEAM_PROPOSAL`，默认由座位 0 开始当队长；启用湖女时初始持有者为首任队长右手边玩家。
- 持久化摘要：`games` 保存当前阶段、轮次、胜者、默认模型配置和每局可选项；`players` 保存真实身份、阵营和每席模型覆盖。
- 初始事件：`game_created` 记录公开开局信息，`roles_assigned` 把完整身份放入私有 payload，`private_view_recorded` 为每名玩家记录合法私有视角。

HTTP 创建路由不会等待整个 AI 推进链路。直接调用服务方法 `create_game()` 默认 `auto_advance=True`，才会在返回前推进到真人或其他阻塞点；这与 HTTP 创建的行为不同。

### 7.2 阶段级流程

一轮任务由组队、发言、投票、任务行动和任务结算组成。不是每轮都会完整经过这些阶段：第五次连续组队失败后会触发强制车队，直接从组队进入任务。

| 阶段 | 触发条件 | 当前行动者 | 输入 | 状态变化 | 主要事件 |
| --- | --- | --- | --- | --- | --- |
| `TEAM_PROPOSAL` | 新局、上一轮任务结束、投票未通过 | 当前队长 | `team` 玩家 id 列表 | 普通组队进入 `SPEECH`；强制车队进入 `QUEST` | `team_proposed` |
| `SPEECH` | 普通组队完成 | 从队长开始按座位顺序逐个发言 | `message` | 所有人发言后进入 `VOTING` | `speech` |
| `VOTING` | 发言完成 | 所有玩家 | `approve` 或 `reject` | 全员投票后，多数赞成进入 `QUEST`，否则换队长回到 `TEAM_PROPOSAL` | `vote_cast`、`vote_result` |
| `QUEST` | 投票通过或强制车队 | 被选入车队的玩家 | `success` 或 `fail` | 全部任务行动提交后结算任务结果 | `quest_action_submitted`、`quest_result` |
| `LADY_OF_LAKE` | 启用湖女，第 2/3/4 次任务后且未结束 | 当前湖女持有者 | `target_player_id` | 私有查验并转交湖女，进入下一次组队 | `lady_of_lake_used` |
| `ASSASSINATION` | 任务成功累计 3 次 | 刺客 | `target_player_id` | 刺中梅林恶方胜利，否则善方胜利 | `assassination` |
| `COMPLETE` | 3 次任务失败或刺杀结算 | 无 | 无 | 对局结束 | 无后续规则事件 |

### 7.3 真人动作入口

```text
POST /api/games/{id}/actions
  -> HumanActionRequest.from_payload
  -> GameService 校验 action_type 是否等于 next_human_action
  -> GameStepRunner 调用规则函数，产出 StepResult
  -> GameCommitter 原子保存当前步事件和 games 摘要
  -> GameAdvanceLoop 推进 AI，每一步单独提交
  -> 返回真人过滤视角 GameState
```

动作类型映射：

| `action_type` | 规则函数 | 事件 |
| --- | --- | --- |
| `propose_team` | `propose_team()` | `team_proposed` |
| `speak` | `record_speech()` | `speech` |
| `vote` | `cast_vote()`，必要时 `finalize_vote()` | `vote_cast`、`vote_result` |
| `mission_action` | `submit_quest_action()`，必要时 `finalize_quest()` | `quest_action_submitted`、`quest_result` |
| `assassinate` | `assassinate()` | `assassination` |
| `use_lady_of_lake` | `use_lady_of_lake()` | `lady_of_lake_used` |

真人动作入口只接受当前合法动作。`GameService._next_human_action()` 根据阶段和真人所在位置计算下一步：

- 真人是当前队长且处于 `TEAM_PROPOSAL`：返回 `propose_team`。
- 处于 `SPEECH` 且下一个发言者是真人：返回 `speak`。
- 处于 `VOTING` 且真人尚未投票：返回 `vote`。
- 处于 `QUEST`、真人在车队中且尚未提交任务行动：返回 `mission_action`。
- 处于 `ASSASSINATION` 且真人身份是刺客：返回 `assassinate`。
- 处于 `LADY_OF_LAKE` 且真人持有湖女：返回 `use_lady_of_lake`。
- 其他情况返回 `null`，前端显示等待 AI 或对局已结算。

### 7.4 AI 自动推进

```text
GameService._auto_advance -> GameAdvanceLoop.advance_until_blocked
  -> 如果轮到真人或对局结束，停止
  -> 根据当前 phase 选择 AI 玩家和决策类型
  -> 解析该玩家模型配置
  -> AiPlayer 构建上下文并调用模型
  -> schemas 校验模型输出
  -> GameStepRunner 使用 rules.py 再次裁判动作
  -> GameCommitter 提交规则事件、决策、记忆、审计和摘要
  -> 继续循环，最多 100 步
```

特殊点：

- 善方 AI 在任务阶段固定提交 `success`，不调用模型。
- 真人也可以通过 `/ai-actions/human` 让 AI 用真人合法私有视角代打当前动作。
- 对未配置或不存在的模型配置，服务层会构造 `unconfigured` profile；实际 provider 调用通常会失败并走错误记录路径。

AI 自动推进和真人动作共用同一套规则函数。因此模型输出只是“动作建议”，最终是否合法仍由 `rules.py` 判断。AI 成功决策会写入三处：

- `ai_decisions` 表：结构化审计记录，便于按对局查询。
- `ai_memory_snapshots` 表：当前简化的记忆快照。
- `game_events` 的 `ai_decision` 事件：当前公开 payload 包括策略摘要，私有 payload 保存 prompt messages、原始输出、解析结果和上下文摘要。私有理由被放入公开摘要是待修复缺口，不是允许公开的游戏事实。

### 7.5 轮次结算细节

投票结算：

- `vote_cast` 在每名玩家投票时追加。
- 所有玩家投票完成后调用 `finalize_vote()`。
- 赞成票数大于玩家总数一半时车队通过，`vote_result.public_payload.approved=true`，阶段进入 `QUEST`。
- 未过半时车队失败，`failed_team_votes` 增加，队长顺延，阶段回到 `TEAM_PROPOSAL`。
- `failed_team_votes >= 5` 时 `forced_team=true`，下一次队长提队后跳过发言和投票，直接进入任务。

任务结算：

- `quest_action_submitted.public_payload` 只记录提交者，真实 `mission_action` 存在私有 payload。
- 所有车队成员提交后，服务层统计 `success_cards` 与 `fail_cards`。
- `finalize_quest()` 按当前任务的 `fail_cards_required` 判定任务成败。
- `quest_result.public_payload.quest_results` 是截至当前轮的成功/失败序列。
- 任务失败累计 3 次直接进入 `COMPLETE`，胜者为恶方。
- 任务成功累计 3 次进入 `ASSASSINATION`，等待刺客选择梅林目标。

刺杀结算：

- `ASSASSINATION` 阶段只允许刺客行动。
- 刺客不能选择自己。
- 目标是 Merlin 时恶方胜利，否则善方胜利。
- `assassination` 事件公开刺客、目标和胜者。

### 7.6 状态恢复

当内存态 `_states` 中没有某个对局时：

```text
GameService._restore_state
  -> GameStateLoader.load
  -> 读取 games 的人数和可选项、players 的真实玩家
  -> 使用当前 STANDARD_MISSION_CONFIGS
  -> 以持久化玩家重建初始 GameState
  -> 按 event_index 重放 game_events
  -> 忽略审计型事件：game_created / roles_assigned / private_view_recorded / ai_decision
  -> 对规则事件调用对应 rules.py 函数
```

恢复任务行动依赖私有 `mission_action`，湖女事件依赖私有 `target_faction`；不能从运行数据库清理这些 payload。已知规则错误会记录最后成功及首个失败序号，并转为 `GameReplayError`，不静默截断后继续。

状态恢复同时依赖建局元数据、玩家真相和规则历史，不是只靠事件表恢复。`status=error_paused`、归档和配置等管理字段不能仅由规则重放重建。当前也没有后台任务恢复：重启后若仍是 `active` 且轮到 AI，GET 不推进，`retry_paused_game()` 又只处理暂停状态，可能停住。

## 8. 信息流与复盘架构

### 8.1 信息源

项目里“信息流”不是单独的数据表，而是由 `game_events` 派生的展示结构。规则事件配合建局事实是状态恢复依据，前端信息流只是可读化视图。

```text
规则/服务层动作
  -> EventStore.append_event 写入 game_events
  -> GameService._public_state 返回过滤后的 events
  -> 前端 applyActiveGame 合并状态内 events 与 /events 拉取结果
  -> flowReview.buildFlowReview 按 event_index 转换为轮次复盘与广播
  -> GameFlow / ReplayDetail 展示信息流
```

同一个事件有四种消费方式：

- 实时对局：`GameState.events` 随状态响应返回，前端立即展示。
- 事件查询：`GET /api/games/{id}/events` 按 `event_index` 返回事件列表，默认公开过滤。
- 日志导出：`GET /api/games/{id}/export` 返回对局摘要和事件序列，默认不包含私有 payload。
- SSE：`GET /api/games/{id}/events/stream?after=N` 补发后续事件，再等待提交通知；没有新事件时发送心跳。

### 8.2 事件可见性

`EventStore` 保存完整事件，`event_visibility.public_event_dicts()` 负责生成公开版本：

- 移除每条事件的 `private_payload`。
- 对尚未出现后续 `vote_result` 的 `vote_cast` 移除 `public_payload.vote`。
- 已结算投票在历史事件中可以公开票型，因为 `vote_result` 已经出现。
- 任务行动的具体 `success` / `fail` 不在公开 `quest_action_submitted` 中；任务结算后通过 `quest_result` 公开成功牌和失败牌数量。

这套过滤同时服务真人前端和 AI 公开事件历史，但不保证公开 payload 内的所有字段都适合公开。当前私有策略摘要和审计访问边界问题见[已知缺口](./backend-modules.md#已知缺口)。

### 8.3 事件目录

| 事件类型 | 何时产生 | 公开 payload | 私有 payload | 信息流处理 |
| --- | --- | --- | --- | --- |
| `game_created` | 新对局保存后 | 对局 id、玩家数、轮次、阶段 | 无 | 显示“对局创建”系统广播。 |
| `roles_assigned` | 角色分配后 | 玩家数 | 完整角色和阵营映射 | 显示“身份已分配”系统广播；不在普通信息流暴露真相。 |
| `private_view_recorded` | 新局为每名玩家记录视角 | `viewer_player_id` | 该玩家合法私有视角 | 普通复盘跳过，用于审计。 |
| `team_proposed` | 队长提交车队 | 队长 id、车队成员 id 列表 | 无 | 生成车队记录和“提交车队”广播。 |
| `speech` | 玩家发言 | 玩家 id、发言文本 | 无 | 生成发言记录和广播。 |
| `vote_cast` | 玩家投票 | 玩家 id、票值；未结算时公开过滤会隐藏票值 | 无 | 先进入前端 `pendingVotes`，等待 `vote_result` 聚合。 |
| `vote_result` | 全员投票后 | 是否通过、连续拒绝次数 | 无 | 生成投票复盘，列出赞成/反对阵营。 |
| `quest_action_submitted` | 任务队员提交行动 | 玩家 id | `mission_action` | 普通公开流不展示行动；私有日志页可展示具体成功/失败。 |
| `quest_result` | 任务行动全员提交后 | 任务结果序列、成功牌数、失败牌数、阶段、胜者 | 无 | 生成任务结算广播；若阶段回到 `team_proposal`，前端轮次加 1。 |
| `lady_of_lake_used` | 湖女查验后 | 查看者、目标、下一持有者和任务轮次 | 目标真实阵营 | 公开广播不展示查验结果。 |
| `assassination` | 刺客选择目标后 | 刺客 id、目标 id、胜者 | 无 | 生成刺杀结算广播并写入当前轮复盘。 |
| `ai_decision` | AI 决策成功或失败后 | 玩家、阶段、决策类型、校验状态、策略摘要 | prompt、原始输出、解析结果、上下文摘要或错误 | 普通复盘跳过；日志页可展开 AI 提示词。 |

### 8.4 前端实时信息流

实时对局在 `App.tsx` 中维护两份相关状态：

- `game`：后端返回的当前公开对局状态。
- `activeGameEvents`：用于桌面信息流的事件列表。

`applyActiveGame()` 先使用 `updated.events` 更新显示，再尝试调用 `listGameEvents(updated.id)` 拉取更完整事件。`eventsForFlowReview()` 比较两个事件列表的最大 `event_index`，保留较新的列表，避免异步请求把更新的事件流覆盖成旧版本。

活动对局同时使用 `EventSource` 订阅 `game-event`，合并增量事件后短暂合并刷新请求，再读取对局状态。当前 SSE 不直接推送完整状态；状态和事件仍可能由不同时间点的请求返回。

桌面展示链路：

```text
GameDesk
  -> GameFlow
  -> buildFlowReview(events, playerNames)
  -> feedItemsForDisplay(review)
  -> InformationFlowPanel
  -> InformationFeedItemView
```

`buildFlowReview()` 的核心规则：

- 总是按 `event_index` 排序，保证事件顺序稳定。
- `game_created`、`roles_assigned` 转为系统广播。
- `private_view_recorded`、`ai_decision` 不进入普通复盘信息流。
- `team_proposed` 写入当前轮车队，并生成广播。
- `speech` 写入当前轮发言，并生成广播。
- `vote_cast` 暂存到 `pendingVotes`，直到 `vote_result` 出现后一次性生成票型复盘。
- `quest_action_submitted` 只有在事件包含私有 payload 时才展示具体任务行动。
- `quest_result` 写入任务结算；如果后端返回阶段为 `team_proposal`，说明对局进入下一轮，前端递增 `currentRoundNumber`。
- `assassination` 写入当前轮刺杀结果。

### 8.5 房间复盘与导出

房间复盘和实时对局使用同一个 `buildFlowReview()`，但请求策略不同：

- 实时对局通常使用公开事件，不展开私有任务行动或 AI prompt。
- 房间页 `showEvents()` 调用 `getRoomDetail(gameId)`，一次取得状态、完整事件、AI 决策和用量统计。
- 玩家名称从详情内的对局状态读取，用于将内部 ID 转成展示名称。
- `ReplayDetail` 展示按轮次聚合后的完整复盘。
- `event-panel` 展示原始事件 payload；`quest_action_submitted` 会把私有 `mission_action` 合并显示。
- `AiPromptDetails` 从 `event.private_payload.prompt_messages` 读取并展开 AI prompt。
- `showExport()` 调用 `exportGame(gameId, true)`，把包含私有 payload 的完整 JSON 输出到导出面板。

普通对局信息流面向玩家，房间复盘面向完整审计。当前服务端 `/room` 和私有导出未以完成态隔离；前端只在完成态展示部分内容不能阻止其他客户端读取。

## 9. 当前过滤与边界缺口

- 真实模型配置写入 `config/llm_profiles.json` 或 `SOLOAVALON_LLM_CONFIG` 指向的本地文件，`config/` 被 git 忽略。
- `config.example/llm_profiles.json` 只能放示例值。
- SQLite 只保存模型 profile id 和模型名，不保存 API Key。
- 模型连通性测试失败时会把错误消息中的 API Key 替换为 `[redacted]`。
- API 公开事件默认不包含 `private_payload`。
- 公开过滤会隐藏尚未结算的投票票值。
- AI prompt 使用公开事件历史和合法私有视角，不直接接收完整隐藏真相。
- `/room`、私有事件查询和私有导出属于完整审计入口，当前没有阻止读取进行中对局。

尚不能声称“前端只接收到合法信息”：列表中的有序 `role_set`、公开 `ai_decision.strategy_summary` 都可能带出隐藏信息。后续需以响应白名单和完成态审计边界收口，而不是仅删除 `private_payload` 或隐藏 UI。

## 10. 开发与验证模块

后端测试按层组织：

| 路径 | 覆盖重点 |
| --- | --- |
| `tests/game/` | 规则建局、身份视角、组队投票、任务和刺杀结算、事件 payload。 |
| `tests/services/` | `GameService` 编排、AI 自动推进、AI 审计、状态恢复、日志导出。 |
| `tests/storage/` | SQLite schema、仓储行为、级联删除、模型配置文件仓储。 |
| `tests/ai/` | 上下文过滤、稳定前缀、prompt 结构、provider 和输出解析错误。 |
| `tests/llm/` | 模型配置类型、校验和 API Key 脱敏。 |
| `tests/api/` | API payload 规范化、对局 API、事件可见性、模型配置 API。 |

前端测试：

- `frontend/src/flowReview.test.ts` 覆盖事件转复盘逻辑。
- `frontend/src/apiKey.test.ts` 覆盖密钥展示脱敏函数。
- `frontend/src/App.test.ts` 主要检查源码字符串和样式约定，不是运行组件交互测试。

后端 API 测试主要覆盖适配对象与错误包装，尚不能替代真实 HTTP 和任务生命周期的集成验证。模型测试中的字符串替身与真实 `LlmCompletionResult` 返回类型不一致，是当前遗漏的契约问题之一。

常用验证命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
npm run test
```

## 11. 扩展点

可优先扩展的位置：

- 自定义身份编辑器：当前只支持 5-10 人推荐组合和有限开关，尚不支持任意身份池编辑。
- 新身份：扩展 `Role`、`faction_for_role()`、`private_view_for_player()`、prompt 配置、前端 `roleLabel()`。
- AI 重试：当前已有部分网络／空输出重试、解析重试和暂停后的人工重试；没有 fallback，也没有中断后的持久化任务恢复。
- Prompt 模板产品化：可把 `config.example/prompt_templates.json` 复制到本地可编辑路径，并用 `SOLOAVALON_PROMPT_CONFIG` 指向。
- AI 审计 UI：完成态已能展开 Prompt 和决策，但记忆留档尚未进入决策；新增记忆能力需独立验证其作用，不作为当前重构前提。

架构优化优先修复公开边界与接口契约，再统一建局事务、恢复推进意图，最后拆分命令／查询和后台推进职责。详细目标与兼容性影响见[后端重设计方案](./game-service-redesign.md)，不是先更换技术栈或数据库。

## 12. 维护注意事项

- 修改规则时，优先补 `tests/game/`，再补服务和 API 测试。
- 修改可见性过滤时，同时检查前端、AI prompt 和日志导出是否仍只看到合法信息。
- 修改模型配置时，不要把 API Key 写入 SQLite、示例配置或测试快照。
- 修改事件 payload 时，需要同步检查状态恢复、`flowReview.ts` 和日志导出。
- 修改前端动作控件时，应以 `next_human_action` 和后端返回状态为准，不在前端复制规则裁判。
