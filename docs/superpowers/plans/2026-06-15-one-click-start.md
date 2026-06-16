# One-Click Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows one-click startup entry that prepares dependencies, starts the FastAPI backend and Vite frontend, writes logs to `logs/`, and opens the local app.

**Architecture:** `start.bat` is the user-facing root entry. `scripts/start-dev.ps1` contains the maintainable startup workflow and uses background processes with redirected stdout/stderr. README documents the entry in Chinese.

**Tech Stack:** Windows batch, PowerShell, Python `unittest`, FastAPI/Uvicorn, Vite/npm.

---

### Task 1: Startup Contract Test

**Files:**
- Create: `tests/test_startup_entry.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupEntryTests(unittest.TestCase):
    def test_windows_entry_and_script_are_present(self) -> None:
        start_bat = ROOT / "start.bat"
        start_ps1 = ROOT / "scripts" / "start-dev.ps1"

        self.assertTrue(start_bat.exists(), "start.bat should exist at the repository root")
        self.assertTrue(start_ps1.exists(), "scripts/start-dev.ps1 should contain startup logic")

        bat_text = start_bat.read_text(encoding="utf-8")
        ps_text = start_ps1.read_text(encoding="utf-8")

        self.assertIn("scripts\\start-dev.ps1", bat_text)
        self.assertIn("backend-dev.out.log", ps_text)
        self.assertIn("frontend-dev.out.log", ps_text)
        self.assertIn("127.0.0.1:8000", ps_text)
        self.assertIn("127.0.0.1:5173", ps_text)
        self.assertIn("Start-Process", ps_text)

    def test_readme_documents_one_click_start(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("一键启动", readme)
        self.assertIn("start.bat", readme)
        self.assertIn("scripts/start-dev.ps1", readme)
        self.assertIn("logs/", readme)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: fail because `start.bat` and `scripts/start-dev.ps1` do not exist yet.

### Task 2: Startup Script Implementation

**Files:**
- Create: `start.bat`
- Create: `scripts/start-dev.ps1`

- [ ] **Step 1: Implement the batch entry**

`start.bat` calls PowerShell from the repository root and keeps the window open on failure.

- [ ] **Step 2: Implement the PowerShell workflow**

`scripts/start-dev.ps1` creates `logs/`, checks `python` and `npm`, creates `.venv` when missing, installs backend dependencies when required, installs frontend dependencies when missing, checks ports `8000` and `5173`, starts both services with redirected logs, probes readiness, opens the frontend, and prints stop instructions.

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: pass.

### Task 3: README Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Chinese one-click startup instructions**

Add a short `## 一键启动` section before manual backend/frontend setup.

- [ ] **Step 2: Run the startup contract test**

Run: `.venv\Scripts\python.exe -m unittest tests.test_startup_entry -v`

Expected: pass.

### Task 4: Verification And Commit

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run PowerShell syntax check**

Run: `$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts\start-dev.ps1), [ref]$null, [ref]$errors) | Out-Null; if ($errors.Count) { $errors; exit 1 }`

Expected: exit code 0.

- [ ] **Step 2: Run repository checks**

Run: `git diff --check`

Expected: exit code 0.

- [ ] **Step 3: Confirm ignored logs**

Run: `git check-ignore -v logs\backend-dev.out.log`

Expected: `.gitignore` line for `logs/`.

- [ ] **Step 4: Commit implementation**

```powershell
git add start.bat scripts\start-dev.ps1 tests\test_startup_entry.py README.md docs\superpowers\plans\2026-06-15-one-click-start.md
git commit -m "feat: add one-click startup entry"
```
