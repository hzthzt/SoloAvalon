# SoloAvalon 一键启动入口设计

## 背景

当前项目需要分别启动 FastAPI 后端和 Vite 前端。用户希望在项目根目录提供一个一键启动入口，降低本地试玩和测试成本。

## 目标

- 在仓库根目录提供双击友好的 `start.bat`。
- 将主要启动逻辑放在 `scripts/start-dev.ps1`，便于维护和调试。
- 自动检查并准备后端 `.venv` 和前端 `node_modules`。
- 同时启动后端 `127.0.0.1:8000` 和前端 `127.0.0.1:5173`。
- 启动后自动打开 `http://127.0.0.1:5173`。
- 后端和前端日志统一写入 `logs/`，该目录不加入 git。

## 非目标

- 不内置或写入任何真实模型配置、API Key 或本地私密环境变量。
- 不替代生产部署脚本。
- 不实现跨平台启动器；本轮优先支持 Windows。
- 不自动删除现有数据库或日志。

## 推荐方案

采用 `start.bat` + `scripts/start-dev.ps1`：

- `start.bat` 只负责从项目根目录调用 PowerShell 脚本，并在失败时保持窗口可见。
- `scripts/start-dev.ps1` 负责依赖检查、日志目录创建、端口占用提示、服务启动和浏览器打开。
- 日志文件命名为 `logs/backend-dev.out.log`、`logs/backend-dev.err.log`、`logs/frontend-dev.out.log`、`logs/frontend-dev.err.log`。

这个方案兼顾双击体验和可维护性。以后如果要加入更多启动参数，只需要扩展 PowerShell 脚本。

## 启动流程

1. 解析项目根目录。
2. 创建 `logs/`。
3. 检查 `python`、`npm` 是否可用。
4. 如果 `.venv` 不存在，执行 `python -m venv .venv`。
5. 如果 `.venv` 尚未安装项目依赖，执行 `.venv\Scripts\python.exe -m pip install -e ".[dev]"`。
6. 如果 `frontend/node_modules` 不存在，执行 `npm install`。
7. 检查 `8000` 和 `5173` 端口占用。
8. 使用后台进程启动后端和前端，并将 stdout/stderr 写入 `logs/`。
9. 短暂等待后打开 `http://127.0.0.1:5173`。
10. 在当前窗口输出服务地址、日志位置和停止方式。

## 错误处理

- 缺少 `python` 或 `npm` 时，直接给出中文错误提示。
- 依赖安装失败时停止启动流程，并提示查看当前窗口输出。
- 端口已占用时提示用户先关闭旧服务，本轮不自动结束未知进程。
- 服务启动后如果页面无法立即访问，仍保留日志路径供排查。

## 测试策略

- 运行 PowerShell 语法检查。
- 执行脚本的非破坏性检查路径，确认能创建 `logs/`，且不会把日志加入 git。
- 手动运行一键入口，确认后端和前端进程启动、页面可访问。
- 运行 `git status --short --ignored`，确认只有脚本和文档进入 git，日志仍为 ignored。
