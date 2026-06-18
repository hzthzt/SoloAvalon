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

    def test_startup_script_keeps_noisy_install_output_in_logs(self) -> None:
        ps_text = (ROOT / "scripts" / "start-dev.ps1").read_text(encoding="utf-8")

        self.assertIn("backend-install.out.log", ps_text)
        self.assertIn("backend-install.err.log", ps_text)
        self.assertIn("Invoke-LoggedChecked", ps_text)

    def test_startup_script_restarts_project_services_and_finds_fallback_ports(self) -> None:
        ps_text = (ROOT / "scripts" / "start-dev.ps1").read_text(encoding="utf-8")

        self.assertIn("Get-ListeningProcessIds", ps_text)
        self.assertIn("Stop-ProjectServiceProcesses", ps_text)
        self.assertIn("Stop-ProjectServiceOnPort", ps_text)
        self.assertIn("Find-AvailablePort", ps_text)
        self.assertIn("Test-BackendServiceProcess", ps_text)
        self.assertIn("Test-FrontendServiceProcess", ps_text)
        self.assertIn("SOLOAVALON_BACKEND_URL", ps_text)
        self.assertIn("SOLOAVALON_FRONTEND_ORIGIN", ps_text)
        self.assertNotIn("Existing SoloAvalon services are already reachable", ps_text)

    def test_startup_script_checks_model_profile_api_before_reporting_ready(self) -> None:
        ps_text = (ROOT / "scripts" / "start-dev.ps1").read_text(encoding="utf-8")

        self.assertIn('$BackendProfilesUrl = "http://127.0.0.1:$BackendPort/api/llm-profiles"', ps_text)
        self.assertIn('Wait-ForHttp "Backend model profile API" $BackendProfilesUrl', ps_text)

    def test_runtime_configuration_accepts_dynamic_dev_ports(self) -> None:
        vite_text = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        main_text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("SOLOAVALON_BACKEND_URL", vite_text)
        self.assertIn("http://127.0.0.1:8000", vite_text)
        self.assertIn("SOLOAVALON_FRONTEND_ORIGIN", main_text)
        self.assertIn("http://127.0.0.1:5173", main_text)

    def test_readme_documents_one_click_start(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("一键启动", readme)
        self.assertIn("start.bat", readme)
        self.assertIn("scripts/start-dev.ps1", readme)
        self.assertIn("logs/", readme)


if __name__ == "__main__":
    unittest.main()
