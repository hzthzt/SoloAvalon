# SoloAvalon

SoloAvalon 是一个本地单人《阿瓦隆》Web 应用。它支持 5-10 人局：1 名真人玩家，其余为 AI 席位；后端使用确定性的 Python 规则引擎裁判，SQLite 保存对局日志，明文模型配置保存到本地 git 忽略目录，前端提供 React 游戏桌、模型配置和房间复盘界面。

文档入口：[架构总览](docs/architecture.md)、[后端模块与运行机制](docs/backend-modules.md)、[后端重设计方案](docs/game-service-redesign.md)、[领域术语](CONTEXT.md)。最初产品设计保存在 `docs/superpowers/specs/2026-06-15-avalon-ai-design.md`；目标设计与历史规格不等于当前已实现能力。

## 当前能力

- 5-10 人阿瓦隆规则：身份分配、组队、固定顺序发言、投票、任务、刺杀和胜负结算；8-10 人可启用湖中仙女，9-10 人可启用 Tristan / Isolde，另有详细身份提示开关。
- 真人视角：状态、列表与事件使用公开白名单；完整身份与私有审计仅在对局完成后开放。
- AI 决策链路：私有上下文、公开历史、稳定 Prompt 前缀、结构化输出解析和 OpenAI-compatible 模型调用；最终失败后暂停并支持重试，没有确定性 fallback。
- SQLite 持久化：对局、玩家、公开/私有事件、AI 决策和记忆留档、规则状态重放、持久化后台推进意图和 JSON 导出；记忆留档尚未参与后续决策。
- 文件型模型配置：OpenAI-compatible 配置明文保存到被 git 忽略的 `config/llm_profiles.json`。
- 本地 API：对局管理、真人动作、AI 代打、错误重试、房间归档、事件与 SSE、日志导出和 LLM 配置 CRUD。
- React + TypeScript 前端：开局、游玩、AI 代打真人席位、管理模型配置、房间复盘和日志导出。

## 架构概览

简化链路如下：

```text
React 前端
  -> FastAPI 路由
  -> GameApplication 命令 / GameQueries 查询 / GameRunner 后台推进
  -> 确定性规则层 + AI 决策链路 + SQLite 短事务
```

设计与维护边界：

- 后端是唯一规则裁判。
- 普通游玩只返回合法玩家视角；完整审计与游玩查询分开，未完成时请求完整审计返回 409。
- AI prompt 只从合法私有视角和过滤后的公开事件历史构建。
- 公开日志不包含 AI 私有审计，使用独立连续序号；投票票型只在结算事件中公布。
- SQLite 事件日志支持复盘、导出和活跃状态恢复。

模块边界、数据流和实现限制见[架构总览](docs/architecture.md)；命令／查询分离、可恢复后台推进与短事务的设计和实施记录见[重设计方案](docs/game-service-redesign.md)。

## 运行约定与限制

创建和动作接口只等待当前操作提交，不等待后续 AI。状态的 `revision` 用于提交版本检查，`is_advancing` 表示仍有后台工作；前端通过 SSE 同步状态，连续代打会等待本次工作结束或暂停。

后台推进仅支持单进程；进程中断后可恢复普通推进和一次真人代打意图。外部模型请求可能在重启后重新调用，但旧回答和旧错误不会重复写入规则或暂停已经前进的状态。记忆快照目前只作审计留档。

## 目录结构

```text
backend/app/game/        确定性规则、领域模型、事件 payload 构建
backend/app/ai/          AI 上下文、单次决策执行器和决策类型
backend/app/prompting/   prompt 模板和 JSON 输出契约
backend/app/llm/         OpenAI-compatible provider 和模型配置类型
backend/app/storage/     SQLite schema、对局仓储和文件型模型配置仓储
backend/app/services/    对局编排、规则单步、事务提交、恢复、事件过滤与通知
backend/app/api/         FastAPI 路由适配器和请求模型
config.example/prompts/  Markdown 提示词正文（系统、角色、行动）
frontend/src/            React UI、API client、样式
tests/                   按后端层级组织的 unittest
docs/                    架构说明和 Superpowers 规格/计划
```

提示词正文在 `config.example/prompts/` 中按文本项分别维护，每段一个 Markdown 文件；`config.example/prompt_templates.json` 保留文件引用及其顺序、结构化配置和 JSON 输出契约。文件格式与维护方法见[提示词说明](docs/prompting.md)。

## 一键启动

在 Windows 上，直接双击根目录的 `start.bat` 即可启动本地开发环境。

也可以在 PowerShell 中运行：

```powershell
.\start.bat
```

或直接调用维护脚本 `scripts/start-dev.ps1`：

```powershell
.\scripts\start-dev.ps1
```

启动脚本会自动完成这些动作：

- 检查 `python` 和 `npm` 是否可用。
- 不存在 `.venv` 时创建 Python 虚拟环境。
- 后端依赖缺失时安装 `.[dev]`。
- 如果 `.venv` 依赖安装失败但系统 Python 已具备运行依赖，会回退使用系统 Python 启动后端。
- 不存在 `frontend/node_modules` 时执行 `npm install`。
- 默认启动后端 `http://127.0.0.1:8000`。
- 默认启动前端 `http://127.0.0.1:5173` 并打开浏览器；实际端口以脚本输出为准。
- 将后端和前端日志写入 `logs/`，该目录不会加入 git。

常用参数：

```powershell
.\scripts\start-dev.ps1 -SkipInstall
.\scripts\start-dev.ps1 -NoOpen
```

停止服务时，可以使用启动窗口最后输出的 `Get-Process -Id ... | Stop-Process` 命令。脚本会识别并重启本项目旧服务；默认端口被其他程序占用时选择可用端口，并同步设置前端代理和后端 CORS 源。

## 后端准备

创建虚拟环境：

```powershell
python -m venv .venv
```

在网络可用时安装后端依赖：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

如果旧版 pip 会先尝试创建隔离构建环境，可以用下面的命令只安装本地 editable 包：

```powershell
.venv\Scripts\python.exe -m pip install -e . --no-use-pep517 --no-deps
```

运行后端测试：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

启动 API：

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

默认 SQLite 数据库位于仓库根目录的 `soloavalon.sqlite3`。可用环境变量覆盖：

```powershell
$env:SOLOAVALON_DB="E:\path\to\soloavalon.sqlite3"
```

默认模型配置文件位于仓库根目录的 `config/llm_profiles.json`，该目录已被 git 忽略。可用环境变量覆盖：

```powershell
$env:SOLOAVALON_LLM_CONFIG="E:\path\to\llm_profiles.json"
```

模型请求默认遵循运行环境的代理设置，不强制直连。代理环境变量优先；
Windows 下未设置代理环境变量时，读取系统代理。针对 Python 3.10 将系统中
不带协议的 HTTPS 代理地址误补为 `https://` 的问题，模型网络适配层使用
`http://` 连接该代理；显式配置的代理协议保持不变，无需修改系统设置。

## 前端准备

安装前端依赖：

```powershell
cd frontend
npm install
```

执行生产构建：

```powershell
npm run build
```

启动开发服务器：

```powershell
npm run dev
```

Vite 开发服务器会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 本地运行

在一个 PowerShell 窗口启动后端：

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

在另一个 PowerShell 窗口启动前端：

```powershell
cd frontend
npm run dev
```

然后打开 `http://127.0.0.1:5173`。

## 基本玩法

1. 启动 `8000` 端口的后端 API。
2. 启动 `5173` 端口的前端。
3. 打开 `http://127.0.0.1:5173`。
4. 在模型页保存可用的模型配置，然后在首页选择模型并创建新对局。
5. 轮到真人玩家时，在动作面板完成组队、发言、投票、任务行动、湖女查验或刺杀。

游玩需要可用模型配置。当前没有离线 fallback；没有配置或调用最终失败时，AI 无法继续正常决策。失败会原子保存审计并进入 `error_paused`，保留推进意图，修复模型配置后可重试。HTTP 创建和动作提交均先返回当前公开状态，后续 AI 由后台任务推进并通过 SSE 通知。

## AI 测试控制

游戏页提供便于测试的 AI 代打能力：

- `AI 代打一步`：只让 AI 提交一次当前真人动作。
- `连续代打`：持续让 AI 提交真人动作，直到对局结束或触发安全步数上限。
- `AI 接管真人`：新建对局后立即让 AI 接管真人席位。

这些能力仍然使用真人玩家的合法私有视角，动作也仍然经过后端规则层校验。

## 模型配置

模型页面可创建、编辑、删除和测试 OpenAI-compatible Chat Completions 配置。配置字段包括：

- `base_url`
- `api_key`
- `model`
- `reasoning_effort`
- `timeout`
- `transport_retries`
- `parse_retries`

编辑已有配置时，如果 `api_key` 留空，会保留原密钥。公开 API 响应和前端只展示脱敏后的 key。

模型调用使用 OpenAI Python SDK，支持自定义 `base_url` 和模型名称；配置输入与文件内容统一使用 Pydantic 校验。配置页面和 JSON 文件保存方式不变，不需要独立网关或管理后台。`base_url` 推荐填写 API 前缀（如 `https://api.example.com/v1`），同时兼容已有配置中的完整 `/chat/completions` 地址。

`reasoning_effort` 可选值为 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 和 `max`，具体支持范围取决于模型。旧配置中的 `temperature` 会被忽略，并按 `medium` 兼容读取；再次保存配置后只保留 `reasoning_effort`。

配置文件格式可参考 `config.example/llm_profiles.json`。真实 API Key 应放入 `config/llm_profiles.json` 或 `SOLOAVALON_LLM_CONFIG` 指向的本地文件，不要放入仓库跟踪文件。

`transport_retries` 和 `parse_retries` 默认均为 5，表示首次尝试后的额外次数。前者只处理明确捕获的超时与空回答；后者处理输出解析或决策校验失败。SDK 自带网络／HTTP 状态码重试关闭，任意网络错误不会被统一自动重试。旧文件的 `timeout_retries` 分别补足缺失的新字段，重新保存后只写新字段。

## API 概览

对局 API：

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

`/actions` 和 `/ai-actions/human` 请求体必须包含非负整数 `expected_revision`。版本过期返回 409 和 `detail.current_revision`，非法动作返回 400。SSE 包含带公开事件 ID 的 `game-event`，以及不推进游标的 `game-state`（最新状态、版本及后台推进标记）。GET 查询不触发续局，`/retry` 用于显式恢复暂停或活跃 AI 回合。

模型配置 API：

- `GET /api/llm-profiles`
- `POST /api/llm-profiles`
- `PUT /api/llm-profiles/{profile_id}`
- `DELETE /api/llm-profiles/{profile_id}`
- `POST /api/llm-profiles/{profile_id}/test`

## 数据与隐私

- 本地 SQLite 默认写入 `soloavalon.sqlite3`，该文件已被 git 忽略。
- 模型配置默认写入 `config/llm_profiles.json`，`config/` 已被 git 忽略。
- `.venv`、前端构建产物、日志、SQLite 文件和真实模型配置目录已被 git 忽略。
- 不要提交真实 API Key 或本地模型供应商配置；`config.example/` 只能放示例值。
- 默认日志导出不包含私有事件 payload。
- `vote_cast` 始终不含票值，结算后的完整票型由 `vote_result.votes` 提供。
- `/room`、私有事件和私有导出只在完成态开放；未完成的归档房间仍只提供公开信息。

## 验证

常用验证命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
npm run test
```

当前后端测试使用标准库 `unittest`；前端验证使用 TypeScript、Vite build 和 Node 测试。`gameSession.test.ts` 执行 SSE、版本与后台等待逻辑，`flowReview.test.ts` 验证公开事件复盘；`App.test.ts` 的源码与样式约定检查只是补充，不是完整组件交互测试。文档-only 变更检查内容、链接、路径和命令，不要求重复运行完整测试。
