import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from backend.app.llm.http_client import _windows_https_proxy, create_http_client


class HttpClientTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.registry_values = {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7897"}
        self.registry = SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            OpenKey=MagicMock(),
            QueryValueEx=lambda key, name: (self.registry_values[name], 0),
        )
        self.context.enter_context(patch.dict("sys.modules", {"winreg": self.registry}))
        self.context.enter_context(patch("backend.app.llm.http_client.sys.platform", "win32"))
        self.environment = self.context.enter_context(
            patch("backend.app.llm.http_client.getproxies_environment", return_value={})
        )

    def test_scheme_less_windows_proxy_uses_http_for_https_requests(self):
        for server in (
            "127.0.0.1:7897",
            "http=127.0.0.1:7897;https=127.0.0.1:7898",
        ):
            with self.subTest(server=server):
                self.registry_values["ProxyServer"] = server
                port = 7898 if "=" in server else 7897
                self.assertEqual(_windows_https_proxy(), f"http://127.0.0.1:{port}")

    def test_explicit_proxy_protocol_is_not_rewritten(self):
        for server in (
            "https=https://proxy.example.com:443",
            "https=http://127.0.0.1:7897",
            "http://127.0.0.1:7897",
            "http=127.0.0.1:7897",
        ):
            with self.subTest(server=server):
                self.registry_values["ProxyServer"] = server
                self.assertIsNone(_windows_https_proxy())

    def test_environment_proxy_settings_take_priority(self):
        for environment in (
            {"https": "https://proxy.example.com:443"},
            {"all": "http://proxy.example.com:8080"},
            {"no": "*"},
        ):
            with self.subTest(environment=environment):
                self.environment.return_value = environment
                self.assertIsNone(_windows_https_proxy())
                self.registry.OpenKey.assert_not_called()

    def test_disabled_or_missing_system_proxy_uses_default_client(self):
        self.registry_values["ProxyEnable"] = 0
        self.assertIsNone(_windows_https_proxy())
        self.registry.OpenKey.side_effect = FileNotFoundError
        self.assertIsNone(_windows_https_proxy())

    def test_other_platforms_do_not_read_windows_registry(self):
        with patch("backend.app.llm.http_client.sys.platform", "linux"):
            self.assertIsNone(_windows_https_proxy())
        self.registry.OpenKey.assert_not_called()

    def test_client_adds_only_https_proxy_mount_and_keeps_defaults(self):
        with (
            patch("backend.app.llm.http_client.DefaultHttpxClient") as client,
            patch("backend.app.llm.http_client.HTTPTransport") as transport,
        ):
            create_http_client()
        transport.assert_called_once_with(proxy="http://127.0.0.1:7897")
        client.assert_called_once_with(mounts={"https://": transport.return_value})

    def test_injected_transport_does_not_read_system_proxy(self):
        with create_http_client(httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            self.assertEqual(client.get("https://example.com").status_code, 200)
        self.registry.OpenKey.assert_not_called()
