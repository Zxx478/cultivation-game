"""游戏层 API 的 Pydantic 模型（白名单 + 范围校验）。

只做「形状与类型」校验，业务规则（加点守恒、境界、贡献）在 service 层。
所有字符串都限长，所有 id 都按枚举/正则约束，避免把用户输入带进 SQL/文件名。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ActionKind = Literal["seclusion", "adventure", "gather", "spar", "meditate"]
CombatAction = Literal["attack", "skill", "defend", "item", "flee"]
ItemType = Literal["technique", "pill", "equipment"]
QuestLevel = Literal["low", "mid", "high"]


class StrictModel(BaseModel):
    """禁止未知字段，避免前端误传被静默忽略。"""

    model_config = ConfigDict(extra="forbid")


class RerollRequest(StrictModel):
    """重掷请求（无参数，保留空体）。"""


class BootstrapRequest(StrictModel):
    """创角请求。

    ``physique/talent/wis`` 由前端回显最近一次重掷结果；服务端只认
    ``meta.creation_roll`` 的权威值，这三个字段仅做兼容接收。
    """

    name: str = Field(min_length=1, max_length=12)
    gender: Literal["male", "female", "unknown"] = "unknown"
    personality: Literal["gu_ao", "wen_hou", "jiao_xia", "feng_dian", "zhongyong"] = "zhongyong"
    physique: Optional[str] = Field(default=None, max_length=32)
    talent: Optional[int] = Field(default=None, ge=1, le=10)
    wis: Optional[int] = Field(default=None, ge=10, le=33)
    points: Dict[str, int] = Field(default_factory=dict)


class ActionRequest(StrictModel):
    """行动请求。"""

    kind: ActionKind
    repeat: int = Field(default=1, ge=1, le=10)


class BreakthroughRequest(StrictModel):
    """突破预览/执行。"""

    prep: Dict[str, Any] = Field(default_factory=dict)
    expected_version: Optional[int] = Field(default=None, ge=1)


class EventChooseRequest(StrictModel):
    """事件选项。"""

    instance_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)


class CombatActionRequest(StrictModel):
    """战斗指令。"""

    action_type: CombatAction
    skill_id: Optional[str] = Field(default=None, max_length=64)
    item_id: Optional[str] = Field(default=None, max_length=64)


class TechniqueRequest(StrictModel):
    """功法操作。"""

    tech_id: str = Field(min_length=1, max_length=64)
    slot: Optional[str] = Field(default=None, max_length=16)


class InventoryRequest(StrictModel):
    """背包操作。"""

    item_id: Optional[str] = Field(default=None, max_length=64)
    instance_id: Optional[int] = Field(default=None, ge=1)
    qty: int = Field(default=1, ge=1, le=999)


class QuestClaimRequest(StrictModel):
    """领取主线奖励。"""

    quest_id: str = Field(min_length=1, max_length=64)


class TravelRequest(StrictModel):
    """前往区域。"""

    region_id: str = Field(min_length=1, max_length=64)


class SectJoinRequest(StrictModel):
    """入宗 / 择散修。"""

    sect_id: str = Field(min_length=1, max_length=64)


class DirectionRequest(StrictModel):
    """定方向。"""

    direction_id: str = Field(min_length=1, max_length=32)


class SectQuestTakeRequest(StrictModel):
    """接宗门任务。"""

    level: QuestLevel


class SectQuestCompleteRequest(StrictModel):
    """完成宗门任务。"""

    quest_id: int = Field(ge=1)


class ExchangeRequest(StrictModel):
    """宗门兑换。"""

    item_type: ItemType
    item_id: str = Field(min_length=1, max_length=64)
    sect_id: Optional[str] = Field(default=None, max_length=64)


class ForbiddenEnterRequest(StrictModel):
    """进入禁地。"""

    land_id: str = Field(min_length=1, max_length=64)


class SaveRequest(StrictModel):
    """保存/读取/导出/删除。"""

    slot_id: int = Field(ge=0, le=3)
    expected_version: Optional[int] = Field(default=None, ge=1)
    label: str = Field(default="", max_length=40)


class ImportRequest(StrictModel):
    """导入存档（JSON 结构）。"""

    payload: Dict[str, Any]
    slot_id: Optional[int] = Field(default=None, ge=1, le=3)


class EmptyRequest(StrictModel):
    """空请求体。"""
