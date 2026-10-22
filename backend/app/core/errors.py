"""统一错误码与异常。

错误码全部来自系统设计 §5（4xxx 游戏层），既有宗门子系统 1xxx–3xxx 原样透传。
api 层只做「错误码 → HTTP 状态」映射，不重复业务判断。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------- 错误码常量 ----------------
# 创角
POINTS_INVALID = 4001          # 加点非法（Σ≠5 / 单项 >3 / 负数）
REROLL_EXHAUSTED = 4002        # 重掷次数用尽
NO_ACTIVE_CHARACTER = 4003     # 无活动角色
VERSION_CONFLICT = 4004        # 乐观锁冲突（HTTP 409）
ACTION_INVALID = 4005          # 行动非法（kind 不在白名单 / repeat 越界）
LIFESPAN_INSUFFICIENT = 4006   # 寿元不足或已坐化
CULTIVATION_INSUFFICIENT = 4007  # 修为不足
BREAKTHROUGH_LIFESPAN_LOW = 4008  # 寿元 < 10 年禁突破
BREAKTHROUGH_COOLDOWN = 4009   # 突破冷却中
NO_PENDING_EVENT = 4010        # 无待决事件
EVENT_OPTION_INVALID = 4011    # 事件选项非法或重放
COMBAT_NOT_FOUND = 4012        # 战斗会话不存在
COMBAT_OWNER_MISMATCH = 4013   # 会话归属不符
COMBAT_ENDED = 4014            # 战斗已结束
BAG_FULL = 4015                # 背包已满
ITEM_INSUFFICIENT = 4016       # 物品不足
TECHNIQUE_NOT_LEARNABLE = 4017  # 功法不可学（境界/越阶限制）
EQUIPMENT_INVALID = 4018       # 装备/槽位非法
SAVE_SLOT_INVALID = 4019       # 存档槽非法
IMPORT_INVALID = 4020          # 导入校验失败（checksum/schema）
CONTENT_VERSION_INCOMPATIBLE = 4021  # 内容版本不兼容
ATTR_REQUIREMENT_NOT_MET = 4022  # 属性未达要求
REGION_LOCKED = 4023           # 区域未解锁
PATH_NOT_ALLOWED = 4024        # 身份不符（散修限定 / 已入宗不可回退）
EVENT_NOT_FOUND = 4025         # 事件不存在
COMBAT_ACTION_INVALID = 4026   # 战斗行动非法（技能/道具不可用）——引擎扩展
FLEE_BANNED = 4027             # 连续逃跑失败 3 次，退路已断——引擎扩展
CONTENT_INVALID = 4028         # 内容层校验失败（启动自检）——引擎扩展
QUEST_NOT_READY = 4029         # 任务目标未达成——引擎扩展
QUEST_UNKNOWN = 4030           # 任务不存在——引擎扩展
INTERNAL_ERROR = 5000          # 内部错误

GAME_ERROR_CODES: Dict[int, str] = {
    POINTS_INVALID: "加点非法",
    REROLL_EXHAUSTED: "重掷次数已用尽",
    NO_ACTIVE_CHARACTER: "无活动角色",
    VERSION_CONFLICT: "版本冲突，请重新载入",
    ACTION_INVALID: "行动非法",
    LIFESPAN_INSUFFICIENT: "寿元不足或已坐化",
    CULTIVATION_INSUFFICIENT: "修为不足",
    BREAKTHROUGH_LIFESPAN_LOW: "寿元不足十年，不可冲关",
    BREAKTHROUGH_COOLDOWN: "冲关冷却未过",
    NO_PENDING_EVENT: "无待决事件",
    EVENT_OPTION_INVALID: "事件选项非法",
    COMBAT_NOT_FOUND: "战斗会话不存在",
    COMBAT_OWNER_MISMATCH: "战斗会话不属于当前角色",
    COMBAT_ENDED: "战斗已结束",
    BAG_FULL: "行囊已满",
    ITEM_INSUFFICIENT: "物品不足",
    TECHNIQUE_NOT_LEARNABLE: "功法不可学",
    EQUIPMENT_INVALID: "装备或槽位非法",
    SAVE_SLOT_INVALID: "存档槽非法",
    IMPORT_INVALID: "存档校验失败",
    CONTENT_VERSION_INCOMPATIBLE: "内容版本不兼容",
    ATTR_REQUIREMENT_NOT_MET: "属性未达要求",
    REGION_LOCKED: "区域未解锁",
    PATH_NOT_ALLOWED: "身份不符",
    EVENT_NOT_FOUND: "事件不存在",
    COMBAT_ACTION_INVALID: "战斗行动非法",
    FLEE_BANNED: "退路已断",
    CONTENT_INVALID: "内容校验失败",
    QUEST_NOT_READY: "任务目标未达成",
    QUEST_UNKNOWN: "任务不存在",
    INTERNAL_ERROR: "内部错误",
}

# 错误码 → HTTP 状态（未列出的 4xxx 一律 400）
CODE_TO_HTTP: Dict[int, int] = {
    POINTS_INVALID: 400,
    REROLL_EXHAUSTED: 429,
    NO_ACTIVE_CHARACTER: 404,
    VERSION_CONFLICT: 409,
    ACTION_INVALID: 400,
    LIFESPAN_INSUFFICIENT: 403,
    CULTIVATION_INSUFFICIENT: 400,
    BREAKTHROUGH_LIFESPAN_LOW: 403,
    BREAKTHROUGH_COOLDOWN: 429,
    NO_PENDING_EVENT: 409,
    EVENT_OPTION_INVALID: 409,
    COMBAT_NOT_FOUND: 404,
    COMBAT_OWNER_MISMATCH: 403,
    COMBAT_ENDED: 409,
    BAG_FULL: 409,
    ITEM_INSUFFICIENT: 402,
    TECHNIQUE_NOT_LEARNABLE: 403,
    EQUIPMENT_INVALID: 400,
    SAVE_SLOT_INVALID: 404,
    IMPORT_INVALID: 400,
    CONTENT_VERSION_INCOMPATIBLE: 409,
    ATTR_REQUIREMENT_NOT_MET: 403,
    REGION_LOCKED: 403,
    PATH_NOT_ALLOWED: 403,
    EVENT_NOT_FOUND: 404,
    COMBAT_ACTION_INVALID: 400,
    FLEE_BANNED: 400,
    CONTENT_INVALID: 500,
    QUEST_NOT_READY: 409,
    QUEST_UNKNOWN: 404,
    INTERNAL_ERROR: 500,
    # 既有宗门子系统 1xxx–3xxx（与 sect_router._HTTP_STATUS_MAP 对齐）
    1001: 400, 1002: 400, 1003: 429, 1004: 403, 1005: 403,
    1006: 404, 1007: 403, 1008: 409, 1009: 410,
    2001: 400, 2002: 402, 2003: 403, 2004: 403, 2005: 404,
    3001: 404, 3002: 403, 3003: 429, 3004: 429, 3005: 402,
}


class GameError(Exception):
    """游戏层业务异常（api 层统一转 ``{ok:false, error:{code,message,detail}}``）。"""

    def __init__(self, code: int, message: str = "", detail: Any = None) -> None:
        """构造异常。

        Args:
            code: 4xxx / 5000 错误码（也允许透传既有 1xxx–3xxx）。
            message: 面向用户的文案；为空时取错误码标准文案。
            detail: 结构化补充信息（缺失条件、缺失 id 清单等）。
        """
        self.code = int(code)
        self.message = message or GAME_ERROR_CODES.get(self.code, "未知错误")
        self.detail = detail
        super().__init__(f"[{self.code}] {self.message}")

    @property
    def http_status(self) -> int:
        """对应 HTTP 状态码。"""
        if self.code in CODE_TO_HTTP:
            return CODE_TO_HTTP[self.code]
        if 4000 <= self.code < 5000:
            return 400
        return 500

    def to_payload(self) -> Dict[str, Any]:
        """转成统一错误体。"""
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def err_payload(err: GameError) -> Dict[str, Any]:
    """把 ``GameError`` 转成 ``error`` 字段结构。"""
    return err.to_payload()


def detail_missing(missing: Optional[list]) -> Optional[Dict[str, list]]:
    """构造「缺失条件」detail（供 4022/4024 等使用）。"""
    if not missing:
        return None
    return {"missing": list(missing)}
