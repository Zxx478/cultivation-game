"""服务层公共上下文：取当前角色。

把散落在各 Service 里的 ``CharacterRepository.get_active() + 4003`` 收拢到一处，
写接口可选要求角色存活（``status != dead``），避免“已死还能炼丹/赶路”。
"""
from __future__ import annotations

from typing import Any, Dict

from ..core.errors import (
    COMBAT_NOT_FOUND,
    LIFESPAN_INSUFFICIENT,
    NO_ACTIVE_CHARACTER,
    NO_PENDING_EVENT,
    GameError,
)
from ..db.game_repository import CharacterRepository, CombatSessionRepository


def active_char(*, alive: bool = False) -> Dict[str, Any]:
    """取当前活动角色。

    Args:
        alive: 为 True 时拒绝已坐化/陨落的角色（4006）。

    Returns:
        角色行 dict。

    Raises:
        GameError: 4003 无角色 / 4006 角色已死。
    """
    char = CharacterRepository.get_active()
    if char is None:
        raise GameError(NO_ACTIVE_CHARACTER, "尚未创建角色")
    if alive and str(char.get("status")) == "dead":
        raise GameError(LIFESPAN_INSUFFICIENT, "你已身死道消，此身不能再动")
    return char


def assert_idle(char: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """写操作前置：待决事件 / 进行中的战斗期间不得改动角色状态。

    Raises:
        GameError: 4010 有待决事件 / 4012 战斗尚未结束。
    """
    target = char if char is not None else active_char(alive=True)
    if str(target.get("pending_event") or ""):
        raise GameError(NO_PENDING_EVENT, "先处理眼前之事")
    if CombatSessionRepository.get(int(target["id"])) is not None:
        raise GameError(COMBAT_NOT_FOUND, "战斗尚未结束")
    return target


def writable_char() -> Dict[str, Any]:
    """取当前角色并校验可写（存活 + 无待决事件 + 无进行中战斗）。"""
    return assert_idle(active_char(alive=True))
