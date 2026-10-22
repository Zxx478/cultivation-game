"""功法 / 背包 / 地图：游戏层进度操作。

功法：参悟升层、装备槽（心法 1 + 攻/防/辅各 3）、学习校验；
背包：丹药/消耗品使用、出售、装备强化（+5）；
地图：20 区域解锁与前往（耗时）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.clock import action_days
from ..core.errors import (
    ACTION_INVALID,
    BAG_FULL,
    EQUIPMENT_INVALID,
    ITEM_INSUFFICIENT,
    REGION_LOCKED,
    TECHNIQUE_NOT_LEARNABLE,
    GameError,
)
from ..core.rng import RngService
from ..db.game_repository import (
    CharacterRepository,
    EquipmentRepository,
    InventoryRepository,
    TechniqueRepository,
)
from ..domain import attrs as attrs_domain
from ..domain import level as level_domain
from .context import active_char
from .save_service import SaveService
from .stats_service import build as build_stats

TIER_BASE = {"fan": 100, "ling": 400, "bao": 1500, "sheng": 8000, "di": 40000}
TIER_PENALTY_BP = {"fan": 0, "ling": 500, "bao": 1000, "sheng": 1800, "di": 2500}
SLOT_CATEGORY = {"heart": "heart", "atk1": "attack", "atk2": "attack", "atk3": "attack",
                 "def1": "defense", "def2": "defense", "def3": "defense",
                 "sup1": "support", "sup2": "support", "sup3": "support"}


def _char() -> Dict[str, Any]:
    """当前角色（存活）。"""
    return active_char(alive=True)


def list_techniques() -> Dict[str, Any]:
    """已学功法 + 装备槽。"""
    char = _char()
    registry = get_registry()
    rows = []
    for row in TechniqueRepository.list_for(int(char["id"])):
        try:
            tech = registry.technique(str(row["tech_id"]))
        except Exception:
            continue
        rows.append({
            "tech_id": str(row["tech_id"]),
            "name": str(tech.get("name", "")),
            "tier": str(tech.get("tier", "")),
            "sub_tier": str(tech.get("sub_tier", "")),
            "type": str(tech.get("type", "")),
            "required_realm_index": int(tech.get("required_realm_index", 1)),
            "layer": int(row.get("layer", 1)),
            "sense": int(row.get("sense", 0)),
            "equipped_slot": str(row.get("equipped_slot") or ""),
            "description": str(tech.get("description", "")),
            "skill": dict(tech.get("skill") or {}),
        })
    equipped = {r["equipped_slot"]: r["tech_id"] for r in rows if r["equipped_slot"]}
    return {"techniques": rows, "equipped": equipped}


def learn(tech_id: str) -> Dict[str, Any]:
    """学习功法（只允许已获得但未入库的功法）。"""
    char = _char()
    registry = get_registry()
    try:
        tech = registry.technique(str(tech_id))
    except Exception:
        raise GameError(TECHNIQUE_NOT_LEARNABLE, f"功法不存在：{tech_id}")
    if int(tech.get("required_realm_index", 1)) > int(char["realm_index"]):
        raise GameError(TECHNIQUE_NOT_LEARNABLE, "境界不足，无法修习")
    if TechniqueRepository.get(int(char["id"]), str(tech_id)) is not None:
        raise GameError(TECHNIQUE_NOT_LEARNABLE, "已经学过该功法")
    # 学习成功率：50% + 悟性×0.2% + 神识×0.05% − 品阶惩罚
    chance = (5000 + int(char["wis"]) * 2 + int(char["sen"]) * 0.5
              - TIER_PENALTY_BP.get(str(tech.get("tier")), 0))
    chance = max(500, min(9500, int(chance)))
    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    ok = rng.roll_bp(chance, stream="technique:learn")
    days = action_days(30, int(char["realm_index"]))
    fields: Dict[str, Any] = {"age_days": int(char["age_days"]) + days,
                              "rng_counter": int(rng.counter) + 1}
    if ok:
        TechniqueRepository.upsert(int(char["id"]), str(tech_id), layer=1)
    elif rng.roll_bp(800, stream="technique:learn:qi"):
        fields["status"] = "qi_deviation"
    CharacterRepository.update(int(char["id"]), fields,
                               expected_version=int(char["version"]))
    SaveService.autosave(int(char["id"]))
    return {"success": bool(ok), "chance_bp": chance, "tech_id": str(tech_id),
            "layer": 1 if ok else 0}


def meditate(tech_id: str) -> Dict[str, Any]:
    """参悟升层（1 个月 / 次）。"""
    char = _char()
    registry = get_registry()
    row = TechniqueRepository.get(int(char["id"]), str(tech_id))
    if row is None:
        raise GameError(TECHNIQUE_NOT_LEARNABLE, "尚未学会该功法")
    try:
        tech = registry.technique(str(tech_id))
    except Exception:
        raise GameError(TECHNIQUE_NOT_LEARNABLE, f"功法不存在：{tech_id}")
    layer = int(row["layer"])
    if layer >= 9:
        return {"tech_id": str(tech_id), "layer": layer, "sense": int(row["sense"]),
                "leveled": False, "maxed": True}
    need = layer * TIER_BASE.get(str(tech.get("tier")), 100)
    gain = int(char["wis"]) * 2 + int(char["sen"])
    sense = int(row["sense"]) + gain
    leveled = False
    if sense >= need:
        sense -= need
        layer += 1
        leveled = True
    days = action_days(30, int(char["realm_index"]))
    TechniqueRepository.upsert(int(char["id"]), str(tech_id), layer=layer, sense=sense,
                               equipped_slot=str(row.get("equipped_slot") or ""))
    CharacterRepository.update(int(char["id"]), {"age_days": int(char["age_days"]) + days},
                               expected_version=int(char["version"]))
    SaveService.autosave(int(char["id"]))
    return {"tech_id": str(tech_id), "layer": layer, "sense": sense,
            "need": layer * TIER_BASE.get(str(tech.get("tier")), 100),
            "gain": gain, "leveled": leveled, "days": days}


def equip(tech_id: str, slot: str) -> Dict[str, Any]:
    """装备功法到槽位。"""
    char = _char()
    slot = str(slot or "")
    if slot not in SLOT_CATEGORY:
        raise GameError(EQUIPMENT_INVALID, f"非法功法槽：{slot}")
    registry = get_registry()
    row = TechniqueRepository.get(int(char["id"]), str(tech_id))
    if row is None:
        raise GameError(TECHNIQUE_NOT_LEARNABLE, "尚未学会该功法")
    tech = registry.technique(str(tech_id))
    if str(tech.get("type")) != SLOT_CATEGORY[slot]:
        raise GameError(EQUIPMENT_INVALID, "功法类型与槽位不符")
    # 同槽先卸下；心法全局唯一
    TechniqueRepository.unequip(int(char["id"]), slot)
    if SLOT_CATEGORY[slot] == "heart":
        for other in TechniqueRepository.list_for(int(char["id"])):
            if str(other.get("equipped_slot") or "") == "heart":
                TechniqueRepository.unequip(int(char["id"]), "heart")
                break
    TechniqueRepository.equip(int(char["id"]), str(tech_id), slot)
    SaveService.autosave(int(char["id"]))
    return {"tech_id": str(tech_id), "slot": slot}


def unequip(tech_id: str, slot: Optional[str] = None) -> Dict[str, Any]:
    """卸下功法（按槽位）。"""
    char = _char()
    if slot and str(slot) in SLOT_CATEGORY:
        TechniqueRepository.unequip(int(char["id"]), str(slot))
    else:
        row = TechniqueRepository.get(int(char["id"]), str(tech_id))
        if row and row.get("equipped_slot"):
            TechniqueRepository.unequip(int(char["id"]), str(row["equipped_slot"]))
    SaveService.autosave(int(char["id"]))
    return {"tech_id": str(tech_id), "slot": slot}


# ---------------- 背包 ----------------

def list_inventory(tab: Optional[str] = None) -> Dict[str, Any]:
    """背包分页。"""
    char = _char()
    registry = get_registry()
    rows = []
    for row in InventoryRepository.list_for(int(char["id"]), item_type=None if tab in (None, "all") else tab):
        try:
            item = registry.item(str(row["item_id"]))
        except Exception:
            continue
        rows.append({
            "item_id": str(row["item_id"]),
            "name": str(item.get("name", "")),
            "kind": str(item.get("kind", row.get("item_type", ""))),
            "tier": str(item.get("tier", "")),
            "qty": int(row["qty"]),
            "stack": int(item.get("stack", 99)),
            "price": int(item.get("price", 0)),
            "description": str(item.get("description", "")),
            "effect": str(item.get("effect", "")),
        })
    equipment = []
    for row in EquipmentRepository.list_for(int(char["id"])):
        try:
            template = registry.equip_template(str(row["template_id"]))
        except Exception:
            template = {}
        equipment.append({
            "instance_id": int(row["instance_id"]),
            "template_id": str(row["template_id"]),
            "name": str(template.get("name", row["template_id"])),
            "slot": str(row["slot"]),
            "rarity": str(row["rarity"]),
            "enhance": int(row["enhance"]),
            "equipped": bool(row["equipped"]),
            "affixes": list(row.get("affixes") or []),
        })
    return {"items": rows, "equipment": equipment, "bag_slots": int(build_stats(char).bag_slots),
            "used_slots": int(InventoryRepository.distinct_slots(int(char["id"])))
            + len([e for e in equipment if not e["equipped"]])}


def use_item(item_id: str, qty: int = 1) -> Dict[str, Any]:
    """使用丹药/消耗品。"""
    char = _char()
    qty = max(1, int(qty or 1))
    inv = InventoryRepository.get(int(char["id"]), str(item_id))
    if inv is None or int(inv["qty"]) < qty:
        raise GameError(ITEM_INSUFFICIENT, f"物品不足：{item_id}")
    registry = get_registry()
    try:
        item = registry.item(str(item_id))
    except Exception:
        raise GameError(ITEM_INSUFFICIENT, f"物品不存在：{item_id}")
    effect = str(item.get("effect"))
    params = dict(item.get("params") or {})
    fields: Dict[str, Any] = {}
    deltas: Dict[str, Any] = {}
    if effect == "gain_cultivation":
        gain = int(params.get("cultivation", 0)) * qty
        realm_end = int(registry.realm_of_level(int(char["level"]))["level_end"])
        applied = level_domain.apply_exp(int(char["level"]), int(char["cultivation"]),
                                         int(char["exp_overflow"]), gain,
                                         lambda lv: registry.exp_k_bp(lv), cap_level=realm_end)
        fields.update({"cultivation": applied.cultivation, "exp_overflow": applied.exp_overflow,
                       "level": applied.level,
                       "realm_index": level_domain.realm_index_of(applied.level),
                       "realm_stage": level_domain.realm_stage_of(applied.level)})
        deltas["cultivation"] = gain
    elif effect == "permanent_attr":
        caps = registry.attrs_config().get("soft_cap", {})
        for key, value in params.items():
            if key in attrs_domain.BASE_KEYS:
                fields[key] = attrs_domain.apply_soft_cap(
                    key, int(char.get(key, 0)) + int(value) * qty, caps)
                deltas[key] = int(value) * qty
    elif effect == "clear_status":
        fields["status"] = "normal"
        deltas["status"] = "normal"
    elif effect == "restore_hp":
        stats = build_stats(char)
        heal = int(stats.derived.hp_max * int(params.get("hp_pct", 30)) / 100) * qty
        fields["hp"] = min(int(stats.derived.hp_max), int(stats.hp) + heal)
        deltas["hp"] = heal
    elif effect == "restore_mp":
        stats = build_stats(char)
        heal = int(stats.derived.mp_max * int(params.get("mp_pct", 50)) / 100) * qty
        fields["mp"] = min(int(stats.derived.mp_max), int(stats.mp) + heal)
        deltas["mp"] = heal
    else:
        raise GameError(ACTION_INVALID, "该物品不能直接使用")
    InventoryRepository.remove(int(char["id"]), str(item_id), qty)
    CharacterRepository.update(int(char["id"]), fields,
                               expected_version=int(char["version"]))
    SaveService.autosave(int(char["id"]))
    return {"item_id": str(item_id), "qty": qty, "deltas": deltas}


def sell_item(item_id: str, qty: int = 1) -> Dict[str, Any]:
    """出售物品。"""
    char = _char()
    qty = max(1, int(qty or 1))
    inv = InventoryRepository.get(int(char["id"]), str(item_id))
    if inv is None or int(inv["qty"]) < qty:
        raise GameError(ITEM_INSUFFICIENT, f"物品不足：{item_id}")
    item = get_registry().item(str(item_id))
    price = int(item.get("price", 0)) * qty
    InventoryRepository.remove(int(char["id"]), str(item_id), qty)
    CharacterRepository.update(int(char["id"]),
                               {"spirit_stone": int(char["spirit_stone"]) + price},
                               expected_version=int(char["version"]))
    SaveService.autosave(int(char["id"]))
    return {"item_id": str(item_id), "qty": qty, "spirit_stone": price}


def equip_equipment(instance_id: int) -> Dict[str, Any]:
    """穿戴装备。"""
    char = _char()
    try:
        slot = EquipmentRepository.equip(int(char["id"]), int(instance_id))
    except GameError:
        raise
    SaveService.autosave(int(char["id"]))
    return {"instance_id": int(instance_id), "slot": slot}


def unequip_equipment(instance_id: int) -> Dict[str, Any]:
    """卸下装备。"""
    char = _char()
    rows = [r for r in EquipmentRepository.list_for(int(char["id"]))
            if int(r["instance_id"]) == int(instance_id)]
    if not rows:
        raise GameError(EQUIPMENT_INVALID, "装备不存在")
    EquipmentRepository.unequip(int(char["id"]), str(rows[0]["slot"]))
    SaveService.autosave(int(char["id"]))
    return {"instance_id": int(instance_id), "slot": str(rows[0]["slot"])}


def enhance(instance_id: int) -> Dict[str, Any]:
    """强化装备（+5 上限，失败不降级）。"""
    char = _char()
    rows = [r for r in EquipmentRepository.list_for(int(char["id"]))
            if int(r["instance_id"]) == int(instance_id)]
    if not rows:
        raise GameError(EQUIPMENT_INVALID, "装备不存在")
    row = rows[0]
    level = int(row["enhance"])
    if level >= 5:
        raise GameError(EQUIPMENT_INVALID, "已强化至 +5 上限")
    cost = 200 * (level + 1)
    if int(char["spirit_stone"]) < cost:
        raise GameError(ITEM_INSUFFICIENT,
                        f"灵石不足（需 {cost}，现有 {int(char['spirit_stone'])}）")
    chance = max(2500, 10000 - level * 500)
    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    ok = rng.roll_bp(chance, stream="equipment:enhance")
    fields: Dict[str, Any] = {"spirit_stone": int(char["spirit_stone"]) - cost,
                              "rng_counter": int(rng.counter) + 1}
    CharacterRepository.update(int(char["id"]), fields,
                               expected_version=int(char["version"]))
    if ok:
        from ..db.repository import transaction
        with transaction() as conn:
            conn.execute("UPDATE character_equipment SET enhance = enhance + 1 "
                         "WHERE instance_id = ? AND char_id = ?",
                         (int(instance_id), int(char["id"])))
    SaveService.autosave(int(char["id"]))
    return {"instance_id": int(instance_id), "success": bool(ok),
            "enhance": level + (1 if ok else 0), "cost": cost, "chance_bp": chance}


# ---------------- 地图 ----------------

def list_map() -> Dict[str, Any]:
    """20 区域（已开放 + 等级门槛）。"""
    char = _char()
    registry = get_registry()
    rows = []
    for region in registry.regions():
        rows.append({
            "id": str(region["id"]),
            "name": str(region.get("name", "")),
            "level_min": int(region.get("level_min", 1)),
            "level_max": int(region.get("level_max", 18)),
            "env_coef": float(region.get("env_coef", 1.0)),
            "env_label": str(region.get("env_label", "")),
            "materials": list(region.get("materials") or ()),
            "material_note": str(region.get("material_note", "")),
            "travel_days": int(region.get("travel_days", 1)),
            "enabled": bool(region.get("enabled")),
            "safe_zone": bool(region.get("safe_zone")),
            "desc": str(region.get("desc", "")),
            "current": str(region["id"]) == str(char.get("region_id")),
            "unlocked": bool(region.get("enabled")) and int(char["level"]) >= int(region.get("level_min", 1)),
        })
    return {"regions": rows, "current": str(char.get("region_id") or "")}


def travel(region_id: str) -> Dict[str, Any]:
    """前往区域（耗时 + 境界门槛）。"""
    char = _char()
    registry = get_registry()
    try:
        region = registry.region(str(region_id))
    except Exception:
        raise GameError(REGION_LOCKED, f"区域不存在：{region_id}")
    if not region.get("enabled"):
        raise GameError(REGION_LOCKED, "此区域尚未开放")
    if int(char["level"]) < int(region.get("level_min", 1)):
        raise GameError(REGION_LOCKED, f"需 Lv.{int(region.get('level_min', 1))} 方可前往")
    days = int(region.get("travel_days", 1))
    CharacterRepository.update(int(char["id"]),
                               {"region_id": str(region_id), "age_days": int(char["age_days"]) + days},
                               expected_version=int(char["version"]))
    SaveService.autosave(int(char["id"]))
    return {"region_id": str(region_id), "region_name": str(region.get("name", "")),
            "days": days, "env_coef": float(region.get("env_coef", 1.0))}
