# SoloAvalon

SoloAvalon 是一个本地单人《阿瓦隆》Web 应用。它支持 5-10 人局：1 名真人玩家，其余为 AI 席位；后端使用确定性的 Python 规则引擎裁判，SQLite 保存对局日志，明文模型配置保存到本地 git 忽略目录，前端提供 React 游戏桌、模型配置和房间复盘界面。

文档入口：[架构总览](docs/architecture.md)、[后端模块与运行机制](docs/backend-modules.md)、[后端重设计方案](docs/game-service-redesign.md)、[领域术语](CONTEXT.md)。最初产品设计保存在 `docs/superpowers/specs/2026-06-15-avalon-ai-design.md`；目标设计与历史规格不等于当前已实现能力。

## 当前能力

- 5-10 人阿瓦隆规则：身份分配、组队、固定顺序发言、投票、任务、刺杀和胜负结算；8-10 人可启用湖中仙女，9-10 人可启用 Tristan / Isolde，另有详细身份提示开关。
- 真人视角：对局状态按真人身份生成可见字段；其他响应仍有已知可见性缺口，见下文。
- AI 决策链路：私有上下文、公开历史、稳定 Prompt 前缀、结构化输出解析和 OpenAI-compatible 模型调用；最终失败后暂停并支持重试，没有确定性 fallback。
- SQLite 持久化：对局、玩家、公开/私有事件、AI 决策和记忆留档、规则状态重放和 JSON 导出；记忆留档尚未参与后续决策。
- 文件型模型配置：OpenAI-compatible 配置明文保存到被 git 忽略的 `config/llm_profiles.json`。
- 本地 API：对局管理、真人动作、AI 代打、错误重试、房间归档、事件与 SSE、日志导出和 LLM 配置 CRUD。
- React + TypeScript 前端：开局、游玩、AI 代打真人席位、管理模型配置、房间复盘和日志导出。

## 架构概览

简化链路如下：

```text
React 前端
  -> FastAPI 路由
  -> GameService 编排层
  -> 确定性规则层 + AI 决策链路 + SQLite 仓储
```

设计与维护边界：

- 后端是唯一规则裁判。
- 普通游玩响应应只包含过滤后的真人视角，完整审计应与游玩接口隔离；当前缺口见下文。
- AI prompt 只从合法私有视角和过滤后的公开事件历史构建。
- 公开日志会移除私有 payload，并隐藏未结算投票的具体票值。
- SQLite 事件日志支持复盘、导出和活跃状态恢复。

模块边界、数据流和实现限制见[架构总览](docs/architecture.md)；命令／查询分离、可恢复后台推进等属于[目标方案](docs/game-service-redesign.md)，尚未实现。

## 已知限制

以下为 2026-10-17 代码审查确认的现状，本次文档更新没有修复代码：

- 对局列表的有序身份字段、公开 AI 决策摘要和进行中完整审计访问仍存在隐藏信息边界缺口。
- 模型连通性测试尚未适配真实 Provider 的返回对象，模型回答成功后也可能报错。
- 重启后能恢复规则状态，但没有恢复后台推进任务；停在 AI 回合的 `active` 对局可能无法通过当前重试入口继续。

原因与代码位置见[后端已知缺口](docs/backend-modules.md#已知缺口)。

## 目录结构

```text
backend/app/game/        确定性规则、领域模型、事件 payload 构建
backend/app/ai/          AI 上下文、单次决策执行器和决策类型
backend/app/prompting/   prompt 模板和 JSON 输出契约
backend/app/llm/         OpenAI-compatible provider 和模型配置类型
backend/app/storage/     SQLite schema、对局仓储和文件型模型配置仓储
backend/app/services/    对局编排、规则单步、事务提交、恢复、事件过滤与通知
backend/app/api/         FastAPI 路由适配器和请求模型
frontend/src/            React UI、API client、样式
tests/                   按后端层级组织的 unittest
docs/                    架构说明和 Superpowers 规格/计划
```

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

游玩需要可用模型配置。当前没有离线 fallback；没有配置或调用最终失败时，AI 无法继续正常决策。失败会保存审计并进入 `error_paused`，修复模型配置后可重试。HTTP 创建对局先返回初始状态，后续 AI 由后台任务推进并通过 SSE 通知。

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
- `timeout_retries`

编辑已有配置时，如果 `api_key` 留空，会保留原密钥。公开 API 响应和前端只展示脱敏后的 key。

模型调用使用 OpenAI Python SDK，支持自定义 `base_url` 和模型名称；配置输入与文件内容统一使用 Pydantic 校验。配置页面和 JSON 文件保存方式不变，不需要独立网关或管理后台。`base_url` 推荐填写 API 前缀（如 `https://api.example.com/v1`），同时兼容已有配置中的完整 `/chat/completions` 地址。

`reasoning_effort` 可选值为 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 和 `max`，具体支持范围取决于模型。旧配置中的 `temperature` 会被忽略，并按 `medium` 兼容读取；再次保存配置后只保留 `reasoning_effort`。

配置文件格式可参考 `config.example/llm_profiles.json`。真实 API Key 应放入 `config/llm_profiles.json` 或 `SOLOAVALON_LLM_CONFIG` 指向的本地文件，不要放入仓库跟踪文件。

`timeout_retries` 默认为 5，保留原字段名：网络请求交给 SDK 按该次数重试连接错误、超时、HTTP 408、409、429 和 5xx；普通 400、401 等错误不会自动重试。AI 空回答、输出解析或决策校验失败使用同一配置值作为独立的输出重试上限。两层上限都表示首次尝试之后的额外次数，网络错误不会再由 AI 层重复重试。

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
- 公开事件流会在投票结果出现前隐藏具体票值。
- 删除私有 payload 不等于已过滤所有隐藏信息；完整审计接口和已知公开字段缺口见[已知限制](#已知限制)。

## 验证

常用验证命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
npm run test
```

当前后端测试使用标准库 `unittest`；前端验证使用 TypeScript、Vite build 和 Node 测试。`App.test.ts` 主要检查源码字符串与样式约定，不是完整交互测试。文档-only 变更检查内容、链接、路径和命令，不要求重复运行完整测试。
