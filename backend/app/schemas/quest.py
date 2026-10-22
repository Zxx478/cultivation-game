"""Schema 层：API 请求/响应模型，基于 Pydantic v2。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------- 通用 ----------


class ErrorResponse(BaseModel):
    code: int
    message: str
    detail: Optional[str] = None


# ---------- 任务接取 ----------


class TakeQuestRequest(BaseModel):
    character_id: int
    sect_id: str = Field(..., description="目标宗门 ID")
    level: str = Field(..., description="low / mid / high")


class TakeQuestResponse(BaseModel):
    quest_id: int
    sect_id: str
    level: str
    quest_kind: str
    target_meta: dict
    reward_contribution: int
    reward_reputation: int
    reward_spirit_stone: int
    expire_at_days: int


# ---------- 任务完成 ----------


class CompleteQuestRequest(BaseModel):
    character_id: int
    quest_id: int


class CompleteQuestResponse(BaseModel):
    quest_id: int
    sect_id: str
    level: str
    contribution_gained: int
    reputation_gained: int
    spirit_stone_gained: int
    new_contribution: int    # 完成后当前贡献点
    new_reputation: int      # 完成后当前好感度（散修才有）
    unlocked_mid: bool       # 本次是否新解锁 mid
    unlocked_high: bool      # 本次是否新解锁 high


# ---------- 兑换 ----------


class ExchangeRequest(BaseModel):
    character_id: int
    sect_id: str
    item_type: str = Field(..., description="technique / pill / equipment / technique_recipe")
    item_id: str
    cost_contribution: int = Field(..., ge=0)
    cost_reputation_threshold: int = Field(0, ge=0, description="散修需达好感度阈值；弟子固定 0")


class ExchangeResponse(BaseModel):
    exchange_id: int
    success: bool
    character_id: int
    sect_id: str
    item_type: str
    item_id: str
    cost_contribution: int
    remaining_contribution: int
    remaining_reputation: Optional[int] = None    # 仅散修返回


# ---------- 禁地探索 ----------


class EnterForbiddenLandRequest(BaseModel):
    character_id: int
    land_id: str
    force_attempt: bool = Field(False, description="测试用，跳过部分检查")


class ForbiddenRewardItem(BaseModel):
    type: str
    item_id: Optional[str] = None
    tech_id: Optional[str] = None
    mat_id: Optional[str] = None
    amount: int = 0
    drop_rate: float = 1.0


class EnterForbiddenLandResponse(BaseModel):
    land_id: str
    role_path: str
    is_first_clear: bool
    boss_name: str
    boss_force_level_coef: float
    rewards: List[ForbiddenRewardItem]
    cost_contribution: int
    remaining_contribution: int
    entry_count_this_year: int


# ---------- 查询 ----------


class CharacterQuestStatusResponse(BaseModel):
    """玩家在某宗门的任务进度快照。"""

    character_id: int
    sect_id: str
    role_path: str
    contribution: int
    reputation: Optional[int] = None    # 仅散修
    low_quests_done: int
    mid_quests_done: int
    high_quests_done: int
    unlocked_mid: bool
    unlocked_high: bool
    daily_taken_quests: int
    daily_cap: int
