# SoloAvalon

SoloAvalon 是一个本地单人《阿瓦隆》Web 应用。它提供标准 5 人局：1 名真人玩家、4 名 AI 席位；后端使用确定性的 Python 规则引擎裁判，SQLite 保存对局日志，明文模型配置保存到本地 git 忽略目录，前端提供 React 游戏桌、模型配置和日志复盘界面。

产品设计源文档：`docs/superpowers/specs/2026-06-15-avalon-ai-design.md`。维护者架构说明：`docs/architecture.md`。

## 当前能力

- 标准 5 人局阿瓦隆规则：身份分配、组队、固定顺序发言、投票、任务、刺杀和胜负结算。
- 真人视角过滤：对局进行中，前端只收到真人玩家合法可见的私有视角。
- AI 决策链路：私有上下文构建、公开事件历史、稳定 prompt 前缀、结构化输出解析、OpenAI-compatible 模型调用和确定性 fallback。
- SQLite 持久化：对局、玩家、公开/私有事件日志、AI 决策、AI 记忆快照、活跃对局恢复和 JSON 导出。
- 文件型模型配置：OpenAI-compatible 配置明文保存到被 git 忽略的 `config/llm_profiles.json`。
- 本地 API：对局管理、真人动作、AI 代替真人测试动作、事件日志、日志导出和 LLM 配置 CRUD。
- React + TypeScript 前端：开局、游玩、AI 代打真人席位、管理模型配置、查看和导出日志。

## 架构概览

简化链路如下：

```text
React 前端
  -> FastAPI 路由
  -> GameService 编排层
  -> 确定性规则层 + AI 决策链路 + SQLite 仓储
```

关键边界：

- 后端是唯一规则裁判。
- 前端只收到过滤后的真人视角，不持有完整隐藏真相。
- AI prompt 只从合法私有视角和过滤后的公开事件历史构建。
- 公开日志会移除私有 payload，并隐藏未结算投票的具体票值。
- SQLite 事件日志支持复盘、导出和活跃状态恢复。

模块边界、数据流、API、存储表、安全边界和扩展点见 [docs/architecture.md](docs/architecture.md)。

## 目录结构

```text
backend/app/game/        确定性规则、领域模型、事件 payload 构建
backend/app/ai/          AI 上下文、fallback 策略、决策执行器
backend/app/prompting/   prompt 模板和 JSON 输出契约
backend/app/llm/         OpenAI-compatible provider 和模型配置类型
backend/app/storage/     SQLite schema、对局仓储和文件型模型配置仓储
backend/app/services/    GameService 编排和事件可见性过滤
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
- 启动后端 `http://127.0.0.1:8000`。
- 启动前端 `http://127.0.0.1:5173` 并打开浏览器。
- 将后端和前端日志写入 `logs/`，该目录不会加入 git。

常用参数：

```powershell
.\scripts\start-dev.ps1 -SkipInstall
.\scripts\start-dev.ps1 -NoOpen
```

停止服务时，可以使用启动窗口最后输出的 `Get-Process -Id ... | Stop-Process` 命令。若 `8000` 或 `5173` 端口已被占用，脚本会提示先关闭旧服务。

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
4. 在首页创建新对局。
5. 轮到真人玩家时，在动作面板完成组队、发言、投票、任务行动或刺杀。

如果没有配置可用的真实模型，AI 席位会使用确定性 fallback 决策。这样本地对局仍然可玩，也能继续验证 OpenAI-compatible 模型配置的结构和调用链路。

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
- `temperature`
- `timeout`

编辑已有配置时，如果 `api_key` 留空，会保留原密钥。公开 API 响应和前端只展示脱敏后的 key。

配置文件格式可参考 `config.example/llm_profiles.json`。真实 API Key 应放入 `config/llm_profiles.json` 或 `SOLOAVALON_LLM_CONFIG` 指向的本地文件，不要放入仓库跟踪文件。

## API 概览

对局 API：

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

## 数据与隐私

- 本地 SQLite 默认写入 `soloavalon.sqlite3`，该文件已被 git 忽略。
- 模型配置默认写入 `config/llm_profiles.json`，`config/` 已被 git 忽略。
- `.venv`、前端构建产物、日志、SQLite 文件和真实模型配置目录已被 git 忽略。
- 不要提交真实 API Key 或本地模型供应商配置；`config.example/` 只能放示例值。
- 默认日志导出不包含私有事件 payload。
- 公开事件流会在投票结果出现前隐藏具体票值。

## 验证

常用验证命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm run build
```

当前后端测试使用标准库 `unittest`；前端验证使用 TypeScript 和 Vite build。
