from __future__ import annotations

from typing import Any


# Public errors contain only curated text, never provider messages or response bodies.
ERRORS = {
    "llm_authentication": ("AuthenticationError", "模型鉴权失败", "模型服务拒绝了身份验证，本次 AI 行动未完成。",
                           "请在模型配置中检查或更新 API Key，然后重试推进。"),
    "llm_permission": ("PermissionDeniedError", "模型访问受限", "当前凭据没有调用该模型或服务的权限。",
                       "请检查账号权限和模型访问范围，然后重试推进。"),
    "llm_rate_limit": ("RateLimitError", "模型限流或额度不足", "模型服务限制了请求，可能达到频率或额度上限。",
                       "请稍后重试，或检查供应商的用量、余额及限流设置。"),
    "llm_timeout": ("TimeoutError", "模型请求超时", "等待模型响应超过时限，本次 AI 行动未完成。",
                    "请稍后重试；若持续发生，请检查网络或调整模型请求超时时间。"),
    "llm_connection": ("APIConnectionError", "模型连接失败", "无法连接模型服务，本次 AI 行动未完成。",
                       "请检查网络、代理和模型服务地址，然后重试推进。"),
    "llm_model_not_found": ("NotFoundError", "模型或接口不存在", "模型服务未找到请求的模型或接口。",
                            "请检查模型名称和服务地址是否正确，然后重试推进。"),
    "llm_request_rejected": ("BadRequestError", "模型请求被拒绝", "模型服务不接受当前请求或参数。",
                             "请检查模型及 reasoning_effort 等参数是否受供应商支持。"),
    "llm_service": ("APIStatusError", "模型服务异常", "模型服务返回异常响应，本次 AI 行动未完成。",
                    "请稍后重试；若持续发生，请检查供应商服务状态或更换模型配置。"),
    "llm_output_invalid": ("ModelOutputError", "模型输出无效", "模型未返回有效内容，或决策格式、内容不符合当前行动要求。",
                           "请重试推进；若反复发生，请检查模型是否适合当前任务，或更换模型。"),
    "llm_configuration": ("ConfigurationError", "模型配置不可用", "当前行动没有可用的完整模型配置，无法调用模型服务。",
                          "请检查对局使用的模型配置、服务地址、API Key 和模型名称。"),
    "advance_limit": ("StepLimitError", "推进达到步数上限", "本次自动推进达到步数上限，已暂停以避免持续执行。",
                      "请检查当前对局进度，确认后可重试推进。"),
    "game_replay": ("GameReplayError", "对局记录异常", "无法从保存的事件记录恢复对局状态。",
                    "重试不会修复损坏的记录，请检查后端日志和对局数据。"),
    "invalid_request": ("ValidationError", "请求或操作无效", "请求参数或操作不符合当前要求，操作未完成。",
                        "请检查输入内容和当前对局阶段，刷新后重试。"),
    "internal_error": ("InternalError", "内部处理错误", "处理当前操作时发生内部错误，操作未完成。",
                       "可先重试；若持续发生，请检查后端日志定位原因。"),
}

TYPE_CODES = {
    "AuthenticationError": "llm_authentication",
    "PermissionDeniedError": "llm_permission",
    "RateLimitError": "llm_rate_limit",
    "APITimeoutError": "llm_timeout",
    "TimeoutError": "llm_timeout",
    "APIConnectionError": "llm_connection",
    "ConnectionError": "llm_connection",
    "NotFoundError": "llm_model_not_found",
    "BadRequestError": "llm_request_rejected",
    "UnprocessableEntityError": "llm_request_rejected",
    "InternalServerError": "llm_service",
    "APIStatusError": "llm_service",
    "EmptyModelOutputError": "llm_output_invalid",
    "JSONDecodeError": "llm_output_invalid",
    "ModelOutputError": "llm_output_invalid",
    "ConfigurationError": "llm_configuration",
    "StepLimitError": "advance_limit",
    "GameReplayError": "game_replay",
}
STATUS_CODES = {400: "llm_request_rejected", 401: "llm_authentication",
                403: "llm_permission", 404: "llm_model_not_found",
                408: "llm_timeout", 422: "llm_request_rejected",
                429: "llm_rate_limit"}


def public_error(code: str) -> dict[str, str]:
    code = code if code in ERRORS else "internal_error"
    error_type, category, message, suggestion = ERRORS[code]
    return {"code": code, "error_type": error_type, "category": category,
            "message": message, "suggestion": suggestion}


def describe_error(exc: Exception, *, ai: bool = False) -> dict[str, str]:
    if type(exc).__name__ in {"AiDecisionError", "GameAdvanceError"} and exc.__cause__ is not None:
        return describe_error(exc.__cause__, ai=ai or type(exc).__name__ == "AiDecisionError")
    error_type = getattr(exc, "error_type", type(exc).__name__)
    status = getattr(exc, "status_code", None)
    code = STATUS_CODES.get(status)
    if code is None and isinstance(status, int) and status >= 500:
        code = "llm_service"
    code = code or TYPE_CODES.get(error_type)
    if code is None and exc.__cause__ is not None:
        return describe_error(exc.__cause__, ai=ai or type(exc).__name__ == "AiDecisionError")
    if code is None and isinstance(exc, (ValueError, KeyError, TypeError)):
        code = "llm_output_invalid" if ai else "invalid_request"
    return public_error(code or "internal_error")


def saved_error(payload: dict[str, Any], *, ai: bool = False) -> dict[str, str]:
    # Rebuild from a known code/type, including legacy rows; never trust saved free text.
    code = payload.get("code")
    if not isinstance(code, str) or code not in ERRORS:
        code = TYPE_CODES.get(str(payload.get("error_type", "")))
    if code is None and ai and payload.get("error_type") in {"ValueError", "KeyError", "TypeError"}:
        code = "llm_output_invalid"
    if code is None and payload.get("error") == "advance step limit exceeded":
        code = "advance_limit"
    return public_error(code or "internal_error")
