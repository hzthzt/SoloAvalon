from __future__ import annotations

from typing import Any

from backend.app.errors import describe_error


def error_detail(exc: Exception) -> dict[str, Any]:
    return describe_error(exc)
