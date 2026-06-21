from __future__ import annotations

import traceback
from typing import Any


def error_detail(exc: Exception, message: str | None = None) -> dict[str, Any]:
    return {
        "message": message if message is not None else _error_message(exc),
        "error_type": type(exc).__name__,
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


def _error_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__
