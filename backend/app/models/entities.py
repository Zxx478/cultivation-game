"""宗门任务+贡献系统领域模型。

只描述实体与状态转换规则，不依赖数据库或 IO。
所有数值阈值来自 app.config.load()。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------- 枚举 ----------------


class RolePath(str, Enum):
    """玩家身份（影响可接任务等级与兑换资格）。"""

    DISCIPLE = "disciple"    # 已入宗弟子
    SANXIU = "sansxiu"       # 散修
    NONE = "none"            # 辟府境前，尚未触发"乱世择门"


class QuestLevel(str, Enum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class QuestStatus(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


# ---------------- 实体 ----------------


@dataclass
class SectMember:
    """弟子身份。一角色同时仅一个宗门。"""

    character_id: int
    sect_id: str
    path_id: str
    joined_at_days: int
    contribution: int = 0
    low_quests_done: int = 0
    mid_quests_done: int = 0
    high_quests_done: int = 0
    unlocked_mid: bool = False
    unlocked_high: bool = False
    is_defected: bool = False
    version: int = 1


@dataclass
class SectReputation:
    """散修对某宗门的独立好感度与贡献点。"""

    character_id: int
    sect_id: str
    reputation: int = 0
    low_quests_done: int = 0
    mid_quests_done: int = 0
    unlocked_mid: bool = False
    contribution: int = 0    # 散修在该宗门累积的"贡献点"，用于兑换
    version: int = 1


@dataclass
class ActiveQuest:
    # 必填字段放前面，避免 dataclass 报"非默认值字段跟在默认值字段之后"
    quest_id: int
    character_id: int
    sect_id: str
    level: QuestLevel
    quest_kind: str
    target_meta: dict
    reward_contribution: int
    accepted_at_days: int
    expire_at_days: int
    # 可选字段
    reward_reputation: int = 0
    reward_spirit_stone: int = 0
    status: QuestStatus = QuestStatus.ACCEPTED


@dataclass
class QuestRecord:
    record_id: int
    character_id: int
    sect_id: str
    level: QuestLevel
    quest_kind: str
    role_path: RolePath
    contribution_gained: int
    reputation_gained: int
    spirit_stone_gained: int
    completed_at_days: int


@dataclass
class ForbiddenLandProgress:
    character_id: int
    land_id: str
    is_first_cleared: bool = False
    entry_count_this_year: int = 0
    last_entry_at_days: Optional[int] = None
    version: int = 1


@dataclass
class ForbiddenLandLog:
    log_id: int
    character_id: int
    land_id: str
    role_path: RolePath
    sect_id: Optional[str]
    attempt: int
    is_first_clear: bool
    rewards_json: str
    success: bool
    entered_at_days: int


@dataclass
class ExchangeLog:
    exchange_id: int
    character_id: int
    sect_id: str
    role_path: RolePath
    item_type: str
    item_id: str
    cost_contribution: int
    cost_reputation: int
    exchanged_at_days: int
