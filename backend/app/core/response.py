"""统一响应包装：``{ok: true, data}`` / ``{ok: false, data: null, error}``。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .errors import GameError


def ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """成功响应。

    Args:
        data: 业务数据（任意可 JSON 序列化对象）。
        **extra: 需要并列返回的顶层键（如 ``request_id``）。
    """
    payload: Dict[str, Any] = {"ok": True, "data": data}
    payload.update(extra)
    return payload


def fail(error: GameError | int | str, message: str = "",
         detail: Any = None) -> Dict[str, Any]:
    """失败响应。

    Args:
        error: ``GameError`` 实例，或错误码，或错误消息字符串。
        message: 当 ``error`` 为错误码时使用。
        detail: 结构化补充信息。
    """
    if isinstance(error, GameError):
        err = error
    elif isinstance(error, int):
        err = GameError(error, message, detail)
    else:
        err = GameError(5000, str(error), detail)
    return {"ok": False, "data": None, "error": err.to_payload()}


def is_ok(payload: Optional[Dict[str, Any]]) -> bool:
    """便捷判断响应是否成功。"""
    return bool(payload) and bool(payload.get("ok"))
