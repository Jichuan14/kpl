"""Typed coach errors that the HTTP layer can localize without leaking internals."""

from __future__ import annotations


class CoachUserInputError(ValueError):
    """Raised for empty, oversized, or otherwise unusable user input."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CoachClassificationError(RuntimeError):
    """Raised when the scope gate cannot be parsed. Tools stay fail-closed."""

    def __init__(self, message: str = "The Draft Coach could not classify this question."):
        super().__init__(message)
        self.code = "coach_classification_error"
        self.message = message


class CoachConversationError(ValueError):
    """Raised for invalid or unauthorized conversation identifiers."""

    def __init__(self, code: str = "conversation_not_found", message: str = ""):
        super().__init__(message or "This conversation is not available.")
        self.code = code
        self.message = message or "This conversation is not available."


class CoachBudgetError(RuntimeError):
    """Raised when the request-wide budget is exhausted before a useful result."""

    def __init__(self, message: str = "The Draft Coach ran out of time."):
        super().__init__(message)
        self.code = "coach_timeout"
        self.message = message


def localized_user_error(code: str, message: str, *, chinese: bool) -> str:
    """Return a safe, localized client message for a known error code."""
    catalog = {
        "empty_input": (
            "请输入一个问题。",
            "Please enter a question.",
        ),
        "input_too_long": (
            "问题过长。请将内容控制在 4000 个字符以内。",
            "The question is too long. Please keep it within 4,000 characters.",
        ),
        "invalid_team_context": (
            "所选战队或赛季无效。",
            "The selected teams or season are not valid.",
        ),
        "invalid_context": (
            "请求上下文无效。",
            "The request context is not valid.",
        ),
        "coach_classification_error": (
            "BP 教练暂时无法判断这个问题，请重试。这并不表示问题与王者荣耀无关。",
            "The Draft Coach could not classify this question. Please try again. This does not mean the question is off-topic.",
        ),
        "coach_unavailable": (
            "BP 教练尚未配置或无法认证。",
            "The Draft Coach provider is not configured or authenticated.",
        ),
        "coach_rate_limited": (
            "BP 教练正忙，请稍后再试。",
            "The Draft Coach is busy. Try again shortly.",
        ),
        "coach_timeout": (
            "BP 教练超时，请重试。",
            "The Draft Coach provider timed out. Try again.",
        ),
        "coach_provider_error": (
            "BP 教练未能完成这次请求。",
            "The Draft Coach provider could not complete the request.",
        ),
        "coach_incomplete": (
            "BP 教练未能在安全限制内完成回答。",
            "The Draft Coach could not finish within its safety limits.",
        ),
        "conversation_not_found": (
            "无法继续该对话，请开始新的提问。",
            "This conversation is not available. Please start a new question.",
        ),
        "conversation_busy": (
            "上一问仍在处理中，请稍后再试。",
            "A previous question is still running. Please wait and try again.",
        ),
    }
    if code in catalog:
        zh, en = catalog[code]
        return zh if chinese else en
    return message
