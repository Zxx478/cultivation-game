"""兑换 Service：弟子与散修的兑换资格校验。

校验逻辑：
1. 通用：贡献点 ≥ cost_contribution
2. 散修额外：
   a. 好感度 ≥ cost_reputation_threshold（即兑换物品所需的最小好感度）
   b. 圣阶及以上功法散修好感度门槛为 high_technique（500）；
   若散修好感度不足，禁止兑换圣阶（含帝阶）功法
3. 弟子兑换无需好感度，但必须属于该宗门（非叛宗状态）

v1.7 升级：
- 物品 tier 分类由"字符串关键词匹配"升级为查 technique/pill/equipment 表的 tier 字段
- 物品不存在时报 ERR_EXCHANGE_ITEM_NOT_FOUND（2005），与 tier 不符区分
- 关键词方案保留为 fallback，仅当物品表无该 item_id 时启用
"""
from __future__ import annotations

from typing import Optional, Tuple

from ..config import load
from ..db import (
    EquipmentRepository,
    ExchangeLogRepository,
    PillRepository,
    SectMemberRepository,
    SectReputationRepository,
    TechniqueRepository,
    transaction,
)
from ..models import RolePath
from . import errors


# ---------------- 物品 tier 查表（v1.7 升级） ----------------


def _classify_item_tier_by_table(item_type: str, item_id: str) -> Optional[Tuple[str, int]]:
    """从数据库查询物品的 tier 与 base_price。

    Returns:
        (tier, base_price) 元组；查不到返回 None。
    """
    if item_type == "technique":
        row = TechniqueRepository.get(item_id)
    elif item_type == "pill":
        row = PillRepository.get(item_id)
    elif item_type == "equipment":
        row = EquipmentRepository.get(item_id)
    else:
        # technique_recipe 类型 v1.7 不查询（仍按关键词 fallback）
        return None

    if row is None:
        return None
    return row["tier"], row["base_price"]


def _classify_item_tier_by_keyword(item_id: str) -> str:
    """Fallback：字符串关键词匹配（兼容 MVP 阶段无完整物品表数据的场景）。

    关键词优先级：圣阶 > 灵阶 > 凡阶。
    """
    item_id_lower = item_id.lower()
    if any(k in item_id_lower for k in ("sheng", "sage", "帝", "圣", "dijie", "shengjie")):
        return "high"
    if any(k in item_id_lower for k in ("ling", "灵", "bao", "宝")):
        return "mid"
    return "low"


def classify_item_tier(item_type: str, item_id: str) -> Tuple[str, Optional[int]]:
    """v1.7 升级版：先查表，查不到回退关键词。

    Returns:
        (tier, base_price_or_None)
        base_price 可能为 None（关键词 fallback 时），调用方应另行传入兑换价
    """
    table_result = _classify_item_tier_by_table(item_type, item_id)
    if table_result is not None:
        return table_result
    # fallback：关键词匹配
    return _classify_item_tier_by_keyword(item_id), None


class ExchangeService:
    """兑换资格校验 + 兑换执行。"""

    @staticmethod
    def _resolve_role(character_id: int) -> tuple:
        member = SectMemberRepository.get(character_id)
        if member is None:
            return RolePath.SANXIU, None
        return RolePath.DISCIPLE, member

    @staticmethod
    def _check_sansxiu_reputation_threshold(role: RolePath, item_tier: str) -> int:
        """返回散修兑换该 tier 物品所需的好感度阈值。弟子返回 0。"""
        if role == RolePath.DISCIPLE:
            return 0
        cfg = load()
        mapping = {
            "low": cfg.sansxiu_exchange_reputation_required.low_technique,
            "mid": cfg.sansxiu_exchange_reputation_required.mid_technique,
            "high": cfg.sansxiu_exchange_reputation_required.high_technique,
        }
        return mapping[item_tier]

    @staticmethod
    def check_and_exchange(
        character_id: int,
        sect_id: str,
        item_type: str,
        item_id: str,
        cost_contribution: int,
        today_day: int,
    ) -> dict:
        """校验 + 执行兑换，事务一致。

        Args:
            cost_contribution: 该物品的兑换价（由调用方从 technique 表查）；
                v1.7 若调用方未传（=0）则自动从物品表查 base_price
        """
        cfg = load()

        # 1. 校验宗门 ID
        if sect_id not in cfg.valid_sect_ids:
            raise errors.SectServiceError(
                errors.ERR_EXCHANGE_INVALID_ITEM,
                f"宗门 ID 非法: {sect_id}",
            )

        # 2. 校验物品类型
        if item_type not in ("technique", "pill", "equipment", "technique_recipe"):
            raise errors.SectServiceError(
                errors.ERR_EXCHANGE_INVALID_ITEM,
                f"物品类型非法: {item_type}",
            )

        # 3. v1.7：查表获取 tier + base_price
        item_tier, base_price = classify_item_tier(item_type, item_id)

        # 4. 若调用方未传 cost_contribution，从物品表取 base_price
        if cost_contribution == 0 and base_price is not None:
            cost_contribution = base_price

        # 5. 若物品在表中查不到且类型非 technique_recipe：报 ITEM_NOT_FOUND
        #    （technique_recipe 走 fallback 关键词，不强制查表）
        if (item_type != "technique_recipe"
                and base_price is None
                and _classify_item_tier_by_table(item_type, item_id) is None):
            raise errors.SectServiceError(
                errors.ERR_EXCHANGE_ITEM_NOT_FOUND,
                f"物品不存在: {item_type}/{item_id}",
                "物品在 technique/pill/equipment 表中查不到；MVP 阶段请先灌入种子或使用 technique_recipe 类型",
            )

        reputation_threshold = ExchangeService._check_sansxiu_reputation_threshold(
            RolePath.SANXIU if ExchangeService._resolve_role(character_id)[0] == RolePath.SANXIU
            else RolePath.DISCIPLE, item_tier,
        )

        role, member_or_none = ExchangeService._resolve_role(character_id)

        with transaction() as conn:
            if role == RolePath.DISCIPLE:
                member = member_or_none
                if member["sect_id"] != sect_id:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_INVALID_ITEM,
                        f"弟子仅可兑换本宗物品（当前宗门: {member['sect_id']}）",
                    )
                if member["contribution"] < cost_contribution:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION,
                        "贡献点不足",
                        f"current={member['contribution']}, required={cost_contribution}",
                    )
                # 扣减贡献点
                member["contribution"] -= cost_contribution
                SectMemberRepository.upsert(member)
                remaining_contribution = member["contribution"]
                remaining_reputation = None
            else:
                # 散修
                rep = SectReputationRepository.get(character_id, sect_id)
                if rep is None:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_INSUFFICIENT_REPUTATION,
                        f"散修未与 {sect_id} 有任何交互（reputation=0）",
                    )
                # 校验贡献点
                if rep["contribution"] < cost_contribution:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION,
                        "贡献点不足",
                        f"current={rep['contribution']}, required={cost_contribution}",
                    )
                # 校验好感度
                if rep["reputation"] < reputation_threshold:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_INSUFFICIENT_REPUTATION,
                        f"散修好感度未达兑换该 tier({item_tier}) 物品的阈值",
                        f"current={rep['reputation']}, required={reputation_threshold}",
                    )
                # 圣阶及以上散修兑换特殊提示（错误码 2004 已由 INSUFFICIENT_REPUTATION 涵盖）
                if item_tier == "high" and rep["reputation"] < cfg.sansxiu_exchange_reputation_required.high_technique:
                    raise errors.SectServiceError(
                        errors.ERR_EXCHANGE_SACRED_TECH_FORBIDDEN_FOR_SANXIU,
                        "散修无法兑换圣阶/帝阶功法（好感度上限未达圣阶门槛）",
                    )
                # 扣减贡献点（注意：不扣减好感度，仅作记录）
                rep["contribution"] -= cost_contribution
                SectReputationRepository.upsert(rep)
                remaining_contribution = rep["contribution"]
                remaining_reputation = rep["reputation"]

            # 记录兑换
            exchange_id = ExchangeLogRepository.insert({
                "character_id": character_id,
                "sect_id": sect_id,
                "role_path": role.value,
                "item_type": item_type,
                "item_id": item_id,
                "cost_contribution": cost_contribution,
                "cost_reputation": reputation_threshold,    # 仅记录阈值
                "exchanged_at_days": today_day,
            })

        return {
            "exchange_id": exchange_id,
            "success": True,
            "character_id": character_id,
            "sect_id": sect_id,
            "item_type": item_type,
            "item_id": item_id,
            "cost_contribution": cost_contribution,
            "remaining_contribution": remaining_contribution,
            "remaining_reputation": remaining_reputation,
        }
