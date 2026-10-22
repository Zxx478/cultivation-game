"""幂等发奖。

设计（系统设计 §4.6 / §4.7 / 风险 R5）：
- 所有奖励发放必须过 ``reward_grant_log`` 的 ``UNIQUE(char_id, source_type, source_ref)``，
  重复调用直接返回 ``granted=False``，**不再产生任何副作用**；
- ``source_ref`` 约定：主线 ``quest:quest_01``、兑换 ``exchange:123``、
  禁地 ``fl:gujing_jianyuan:1``、宗门任务 ``sectquest:88``；
- 发奖只处理「角色侧」状态（修为/灵石/属性/背包/功法/装备/标记），
  宗门贡献由本服务写 ``sect_member`` 或 ``sansxiu_wallet``（权威仍在既有表）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..content import get_registry
from ..core.errors import BAG_FULL, CONTENT_INVALID, GameError
from ..db.game_repository import (
    CharacterRepository,
    EquipmentRepository,
    FlagRepository,
    GrantLogRepository,
    InventoryRepository,
    LogRepository,
    TechniqueRepository,
)
from ..db.repository import SansxiuWalletRepository, SectMemberRepository
from ..domain import attrs as attrs_domain
from ..domain import level as level_domain

# 各类型道具的堆叠上限缺省（items.yaml 的 stack 优先）
DEFAULT_STACK = {"pill": 99, "material": 999, "consumable": 99}


class GrantService:
    """幂等发奖服务。"""

    @staticmethod
    def grant(char_id: int, rewards: Mapping[str, Any], *,
              source_type: str, source_ref: str,
              log_kind: str = "grant") -> Dict[str, Any]:
        """发放一组奖励。

        Args:
            char_id: 角色 id。
            rewards: ``{exp, stones, items[], equipments[], techniques[],
                attrs{}, flags[], contribution}``（缺省键跳过）。
            source_type: 来源类型（quest / exchange / forbidden / sect_quest / event）。
            source_ref: 来源唯一标识（幂等键的一半）。
            log_kind: 事件日志的 kind。

        Returns:
            ``{"granted": bool, "duplicate": bool, "deltas": {...}, "notes": [...]}``

        Raises:
            GameError: 4003 角色不存在 / 4004 版本冲突 / 4015 背包已满 / 4028 内容缺失。
        """
        char = CharacterRepository.require(char_id)
        if GrantLogRepository.exists(char_id, source_type, source_ref):
            return {"granted": False, "duplicate": True, "deltas": {}, "notes": ["重复发奖已忽略"]}

        deltas: Dict[str, Any] = {}
        notes: List[str] = []
        fields: Dict[str, Any] = {}

        # ---- 1. 修为 ----
        exp = int(rewards.get("exp", 0) or 0)
        if exp > 0:
            registry = get_registry()
            realm_end = int(registry.realm_of_level(int(char["level"]))["level_end"])
            result = level_domain.apply_exp(
                int(char["level"]), int(char["cultivation"]), int(char["exp_overflow"]),
                exp, lambda lv: registry.exp_k_bp(lv), cap_level=realm_end)
            fields.update({
                "level": result.level,
                "cultivation": result.cultivation,
                "exp_overflow": result.exp_overflow,
                "realm_index": level_domain.realm_index_of(result.level),
                "realm_stage": level_domain.realm_stage_of(result.level),
            })
            deltas["exp"] = exp
            deltas["level"] = result.level
            deltas["levels_gained"] = result.levels_gained

        # ---- 2. 灵石 ----
        stones = int(rewards.get("stones", 0) or 0)
        if stones:
            fields["spirit_stone"] = max(0, int(char["spirit_stone"]) + stones)
            deltas["stones"] = stones

        # ---- 3. 属性（走软上限） ----
        attr_changes = dict(rewards.get("attrs") or {})
        if attr_changes:
            caps = get_registry().attrs_config().get("soft_cap", {})
            applied: Dict[str, int] = {}
            for key, delta in attr_changes.items():
                if key not in attrs_domain.BASE_KEYS:
                    notes.append(f"未知属性 {key} 已忽略")
                    continue
                current = int(char.get(key, 0))
                new_value = attrs_domain.apply_soft_cap(key, current + int(delta), caps)
                fields[key] = new_value
                applied[key] = new_value - current
            deltas["attrs"] = applied

        # ---- 4. 因果 / 道心（若奖励里带） ----
        if rewards.get("karma"):
            fields["karma"] = max(-1000, min(1000, int(char["karma"]) + int(rewards["karma"])))
            deltas["karma"] = int(rewards["karma"])
        if rewards.get("dao_heart"):
            fields["dao_heart"] = max(0, min(100, int(char["dao_heart"]) + int(rewards["dao_heart"])))
            deltas["dao_heart"] = int(rewards["dao_heart"])

        if fields:
            CharacterRepository.update(char_id, fields, expected_version=int(char["version"]))

        # ---- 5. 道具（堆叠 + 背包上限） ----
        item_deltas: Dict[str, int] = {}
        for entry in rewards.get("items", ()) or ():
            item_id = str(entry.get("item_id"))
            qty = int(entry.get("qty", 1))
            if qty <= 0:
                continue
            item = get_registry().item(item_id)
            kind = str(item["kind"])
            stack = int(item.get("stack", DEFAULT_STACK.get(kind, 99)))
            GrantService._ensure_bag_room(char_id, item_id)
            InventoryRepository.add(char_id, item_id, kind, min(qty, stack * 99))
            item_deltas[item_id] = qty
        if item_deltas:
            deltas["items"] = item_deltas

        # ---- 6. 装备（实例表，不堆叠） ----
        equip_deltas: List[int] = []
        for template_id in rewards.get("equipments", ()) or ():
            template = get_registry().equip_template(str(template_id))
            instance_id = EquipmentRepository.create(
                char_id, str(template_id), slot=str(template["slot"]),
                rarity=str(template.get("rarity") or "common"))
            equip_deltas.append(instance_id)
        if equip_deltas:
            deltas["equipments"] = equip_deltas

        # ---- 7. 功法 ----
        tech_deltas: List[str] = []
        for tech_id in rewards.get("techniques", ()) or ():
            tech = get_registry().technique(str(tech_id))
            if int(tech["required_realm_index"]) > int(char["realm_index"]):
                notes.append(f"《{tech['name']}》境界不足（需第 {tech['required_realm_index']} 境），暂未入库")
                continue
            TechniqueRepository.upsert(char_id, str(tech_id), layer=1)
            tech_deltas.append(str(tech_id))
        if tech_deltas:
            deltas["techniques"] = tech_deltas

        # ---- 8. 标记 ----
        flag_list = [str(x) for x in (rewards.get("flags", ()) or ())]
        for key in flag_list:
            FlagRepository.set(char_id, key, "1")
        if flag_list:
            deltas["flags"] = flag_list

        # ---- 9. 贡献（宗门优先，其次散修钱包） ----
        contribution = int(rewards.get("contribution", 0) or 0)
        if contribution:
            member = SectMemberRepository.get(char_id)
            if member:
                member["contribution"] = int(member["contribution"]) + contribution
                SectMemberRepository.upsert(member)
                deltas["sect_contribution"] = contribution
            else:
                wallet = SansxiuWalletRepository.get(char_id)
                wallet["general_contribution"] = int(wallet["general_contribution"]) + contribution
                wallet["total_earned"] = int(wallet["total_earned"]) + max(0, contribution)
                SansxiuWalletRepository.upsert(wallet)
                deltas["sansxiu_contribution"] = contribution

        GrantLogRepository.record(char_id, source_type, source_ref,
                                  {"deltas": deltas, "notes": notes})
        LogRepository.add_event(char_id, log_kind,
                                {"source_type": source_type, "source_ref": source_ref,
                                 "deltas": deltas})
        return {"granted": True, "duplicate": False, "deltas": deltas, "notes": notes}

    # ---------------- 内部 ----------------

    @staticmethod
    def _ensure_bag_room(char_id: int, item_id: str) -> None:
        """背包未满校验（同物品已有堆叠则不占新格）。

        Raises:
            GameError: 4015 背包已满。
        """
        if InventoryRepository.get(char_id, item_id) is not None:
            return
        char = CharacterRepository.require(char_id)
        used = InventoryRepository.distinct_slots(char_id)
        if used >= int(char["bag_slots"]):
            raise GameError(BAG_FULL, "行囊已满", {"used": used, "slots": int(char["bag_slots"])})

    @staticmethod
    def batch(char_id: int, rewards_list: Iterable[Mapping[str, Any]], *,
              source_type: str, source_ref_prefix: str) -> List[Dict[str, Any]]:
        """批量发奖（每条一个 source_ref，便于逐项幂等）。"""
        out: List[Dict[str, Any]] = []
        for index, rewards in enumerate(rewards_list):
            out.append(GrantService.grant(
                char_id, rewards, source_type=source_type,
                source_ref=f"{source_ref_prefix}:{index}"))
        return out


def exp_need_for(char_id: int) -> int:
    """当前角色当前等级所需修为（便利函数，供 UI 进度条复用）。"""
    char = CharacterRepository.require(char_id)
    return level_domain.exp_need(int(char["level"]), get_registry().exp_k_bp(int(char["level"])))


def floor_or_zero(value: Any) -> int:
    """安全取整（None / 非法 → 0）。"""
    try:
        return int(math.floor(float(value)))
    except (TypeError, ValueError):
        return 0


def require_content(kind: str, ident: str) -> Mapping[str, Any]:
    """按类型取内容（内容缺失抛 4028）。"""
    registry = get_registry()
    try:
        if kind == "item":
            return registry.item(ident)
        if kind == "tech":
            return registry.technique(ident)
        if kind == "equip":
            return registry.equip_template(ident)
    except GameError:
        raise
    raise GameError(CONTENT_INVALID, f"未知内容类型：{kind}", {"kind": kind, "id": ident})
