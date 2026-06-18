# Smart Dev Server Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `start.bat` 每次启动都刷新本项目的前后端服务，并在默认端口被非本项目程序占用时自动选择可用端口。

**Architecture:** 保留 `start.bat` 调用 `scripts/start-dev.ps1` 的结构。PowerShell 脚本负责识别监听端口的进程、只停止 SoloAvalon 旧服务、为外部占用端口寻找后续可用端口，并通过环境变量把实际后端地址和前端来源传给 Vite 与 FastAPI。

**Tech Stack:** PowerShell, Python unittest, Vite, FastAPI CORS middleware.

---

### Task 1: 启动入口契约测试

**Files:**
- Modify: `tests/test_startup_entry.py`
- Test: `tests/test_startup_entry.py`

- [ ] **Step 1: Write the failing test**

```python
def test_startup_script_restarts_project_services_and_finds_fallback_ports(self) -> None:
    ps_text = (ROOT / "scripts" / "start-dev.ps1").read_text(encoding="utf-8")

    self.assertIn("Stop-ProjectServiceOnPort", ps_text)
    self.assertIn("Find-AvailablePort", ps_text)
    self.assertIn("Get-ListeningProcessIds", ps_text)
    self.assertIn("Test-BackendServiceProcess", ps_text)
    self.assertIn("Test-FrontendServiceProcess", ps_text)
    self.assertIn("SOLOAVALON_BACKEND_URL", ps_text)
    self.assertIn("SOLOAVALON_FRONTEND_ORIGIN", ps_text)
    self.assertNotIn("Existing SoloAvalon services are already reachable", ps_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: FAIL because the current script still reuses existing reachable services and lacks the new helpers.

- [ ] **Step 3: Write minimal implementation**

Add PowerShell helpers for listener PID lookup, command-line inspection, project-service detection, global project-service cleanup, selective stop on occupied candidate ports, and fallback port scanning. Recompute URLs after final ports are chosen and set child-process environment variables before starting backend/frontend.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: PASS.

### Task 2: Runtime port propagation

**Files:**
- Modify: `frontend/vite.config.ts`
- Modify: `backend/app/main.py`
- Test: `tests/test_startup_entry.py`

- [ ] **Step 1: Write the failing test**

```python
def test_runtime_configuration_accepts_dynamic_dev_ports(self) -> None:
    vite_text = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    main_text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    self.assertIn("SOLOAVALON_BACKEND_URL", vite_text)
    self.assertIn("http://127.0.0.1:8000", vite_text)
    self.assertIn("SOLOAVALON_FRONTEND_ORIGIN", main_text)
    self.assertIn("http://127.0.0.1:5173", main_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: FAIL because Vite proxy and FastAPI CORS are fixed to default ports.

- [ ] **Step 3: Write minimal implementation**

Read `SOLOAVALON_BACKEND_URL` in Vite config for proxy target and read `SOLOAVALON_FRONTEND_ORIGIN` in FastAPI startup to extend allowed CORS origins.

- [ ] **Step 4: Run focused and build verification**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v
cd frontend
npm run build
```

Expected: both commands pass.
