from __future__ import annotations

import sys
from urllib.request import getproxies_environment

from httpx import BaseTransport, HTTPTransport
from openai import DefaultHttpxClient


def create_http_client(transport: BaseTransport | None = None) -> DefaultHttpxClient:
    if transport is not None:
        return DefaultHttpxClient(transport=transport)
    proxy = _windows_https_proxy()
    if proxy is None:
        return DefaultHttpxClient()
    return DefaultHttpxClient(mounts={"https://": HTTPTransport(proxy=proxy)})


def _windows_https_proxy() -> str | None:
    if sys.platform != "win32" or getproxies_environment():
        return None

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as settings:
            if not winreg.QueryValueEx(settings, "ProxyEnable")[0]:
                return None
            server = str(winreg.QueryValueEx(settings, "ProxyServer")[0]).strip()
    except OSError:
        return None

    if "=" in server:
        address = ""
        for setting in server.split(";"):
            protocol, separator, value = setting.partition("=")
            if separator and protocol.strip().lower() == "https":
                address = value.strip()
                break
    else:
        address = server
    if not address or "://" in address:
        return None
    # Windows 的协议名表示请求类型；无协议的代理地址使用 HTTP CONNECT。
    # Python 3.10 会错误地为 HTTPS 请求补上 https://，只在此处修正。
    return f"http://{address}"
