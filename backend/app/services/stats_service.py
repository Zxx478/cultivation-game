"""角色属性聚合：基础属性 + 装备 + 功法 + 环境 → 衍生属性 / ExpRate / 战斗属性。

为什么单独一层：
- 行动（闭关）、战斗、角色面板、突破预览都需要同一套数值，重复实现必然漂移；
- 本模块只读 content / character 子表，不做任何写操作，方便被单测直接驱动；
- 装备词条、功法层数、体质适配、心境/状态系数全部在这里收口。

数值口径见 ``docs/04-系统设计-v1.5.md`` §4 与 ``docs/03-MVP实现任务书.md`` §8。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..content import get_registry
from ..db.game_repository import (
    CharacterRepository,
    EquipmentRepository,
    SectQuestRecordRepository,
    TechniqueRepository,
)
from ..domain import attrs as attrs_domain


# 装备主词条 → 战斗/面板字段
_MAIN_ATTR = {
    "atk": "atk",
    "def": "def",
    "sen": "sen",
    "luk": "luk",
    "atk_spr": "atk_spr",
    "bag_slots": "bag_slots",
}

# 副词条 → 基础属性（百分比）
_AFFIX_ATTR = {"str", "con", "spr", "sen", "agi", "luk"}
# 副词条 → 衍生（bp / 固定值）
_AFFIX_DERIVED = {"hp", "crit", "crit_dmg", "pen"}


@dataclass
class CharacterStats:
    """一次角色面板计算的完整结果。"""

    char: Dict[str, Any]
    base: Dict[str, int]
    equip: Dict[str, float]
    derived: attrs_domain.DerivedAttrs
    hp: int
    mp: int
    exp_rate: float
    tech_coef_bp: int
    exp_breakdown: Dict[str, Any] = field(default_factory=dict)
    skills: List[Dict[str, Any]] = field(default_factory=list)
    bag_slots: int = 60

    def panel(self) -> Dict[str, Any]:
        """角色面板（10 基础 + 13 衍生 + 战力 + 装备加成）。"""
        return {
            "character_id": int(self.char["id"]),
            "level": int(self.char["level"]),
            "realm_index": int(self.char["realm_index"]),
            "realm_stage": int(self.char["realm_stage"]),
            "base": dict(self.base),
            "derived": self.derived.as_dict(),
            "cp": int(self.derived.cp),
            "hp": int(self.hp),
            "mp": int(self.mp),
            "hp_max": int(self.derived.hp_max),
            "mp_max": int(self.derived.mp_max),
            "exp_rate": float(self.exp_rate),
            "tech_coef_bp": int(self.tech_coef_bp),
            "exp_breakdown": dict(self.exp_breakdown),
            "bag_slots": int(self.bag_slots),
            "equipment_bonus": dict(self.equip),
            "skills": [dict(s) for s in self.skills],
        }

    def combat_player(self) -> Dict[str, Any]:
        """构造战斗引擎所需的玩家属性。"""
        from ..domain.combat import player_from_derived

        return player_from_derived(
            self.derived.as_dict(), hp=self.hp, mp=self.mp,
            skills=self.skills, name=str(self.char.get("name") or "你"))


def _template_of(template_id: str) -> Mapping[str, Any]:
    """按 id 取装备模板（缺失返回空 dict）。"""
    try:
        return get_registry().equip_template(str(template_id))
    except Exception:  # pragma: no cover - 内容缺失时宁可少算不崩
        return {}


def _rarity_of(rarity_id: str) -> Mapping[str, Any]:
    """按 id 取稀有度配置。"""
    for row in get_registry().content.equipments["rarity"]:
        if str(row["id"]) == str(rarity_id):
            return row
    return {"value_multiplier_bp": 10000, "affix_count": 1, "id": "common"}


def _slot_of(slot_id: str) -> Mapping[str, Any]:
    """按 id 取槽位配置。"""
    for row in get_registry().content.equipments["slots"]:
        if str(row["id"]) == str(slot_id):
            return row
    return {}


def _direction_bonus(char: Mapping[str, Any]) -> Tuple[Dict[str, float], Dict[str, int]]:
    """方向树已点亮节点的加成（按累计贡献）。

    只映射引擎能消费的效果：全属性百分比、攻击/暴击/先手/灵力上限；
    其余标记类效果（解控、免死、技能解锁）由战斗/内容层按需读取，不在此处硬算。
    """
    direction = str(char.get("direction_id") or "")
    if not direction:
        return {}, {}
    try:
        total = SectQuestRecordRepository.sum_contribution(int(char["id"]))
    except Exception:
        total = 0
    derived: Dict[str, float] = {}
    base_pct: Dict[str, int] = {}
    try:
        nodes = get_registry().direction_nodes(direction)
    except Exception:
        return {}, {}
    for node in nodes:
        if total < int(node.get("required_total_contribution", 0)):
            continue
        for effect in node.get("effects", ()):
            etype = str(effect.get("type"))
            value = float(effect.get("value", 0) or 0)
            if etype == "attr_pct":
                for key in ("str", "con", "spr", "sen", "agi"):
                    base_pct[key] = base_pct.get(key, 0) + int(value)
            elif etype == "crit_rate_bp":
                derived["crit_bp"] = derived.get("crit_bp", 0.0) + value
            elif etype == "init_flat":
                derived["init"] = derived.get("init", 0.0) + value
            elif etype == "normal_attack_flat":
                derived["atk"] = derived.get("atk", 0.0) + value
            elif etype == "mp_max_pct":
                derived["mp_pct"] = derived.get("mp_pct", 0.0) + value * 100.0
    return derived, base_pct


def _equip_bonus(char_id: int) -> Tuple[Dict[str, float], Dict[str, int], Dict[str, int], int]:
    """汇总已装备实例的主/副词条。

    Returns:
        ``(derived_bonus, base_attr_pct, base_attr_flat, bag_slots_bonus)``。
    """
    derived: Dict[str, float] = {}
    base_pct: Dict[str, int] = {}
    base_flat: Dict[str, int] = {}
    bag_bonus = 0
    for row in EquipmentRepository.list_for(char_id):
        if not int(row.get("equipped", 0)):
            continue
        template = _template_of(str(row["template_id"]))
        rarity = _rarity_of(str(row.get("rarity") or template.get("rarity") or "common"))
        slot = _slot_of(str(row.get("slot") or template.get("slot") or ""))
        sub_tier = str(template.get("sub_tier") or "xia")
        main_values = dict(slot.get("main_values") or {})
        base_main = float(main_values.get(sub_tier, main_values.get("xia", 0)))
        mult = int(rarity.get("value_multiplier_bp", 10000)) / 10000.0
        enhance = int(row.get("enhance", 0))
        main_value = base_main * mult * (1.0 + 0.08 * enhance)

        affix = str(slot.get("main_affix") or "")
        if affix == "atk":
            derived["atk"] = derived.get("atk", 0.0) + main_value
        elif affix == "def":
            derived["def"] = derived.get("def", 0.0) + main_value
        elif affix == "sen":
            base_flat["sen"] = base_flat.get("sen", 0) + int(main_value)
        elif affix == "luk":
            base_flat["luk"] = base_flat.get("luk", 0) + int(main_value)
        elif affix == "atk_spr":
            derived["atk"] = derived.get("atk", 0.0) + main_value
            base_flat["spr"] = base_flat.get("spr", 0) + int(main_value)
        elif affix == "bag_slots":
            bag_bonus += int(main_value)

        for entry in row.get("affixes") or ():
            if not isinstance(entry, Mapping):
                continue
            key = str(entry.get("id") or entry.get("key") or "")
            value = float(entry.get("value", entry.get("value_pct", 0)) or 0)
            if key in _AFFIX_ATTR:
                base_pct[key] = base_pct.get(key, 0) + int(value)
            elif key == "hp":
                derived["hp_pct"] = derived.get("hp_pct", 0.0) + value * 100.0
            elif key == "crit":
                derived["crit_bp"] = derived.get("crit_bp", 0.0) + value * 100.0
            elif key == "crit_dmg":
                derived["crit_dmg_bp"] = derived.get("crit_dmg_bp", 0.0) + value * 100.0
            elif key == "pen":
                derived["pen"] = derived.get("pen", 0.0) + value * 10.0
    return derived, base_pct, base_flat, bag_bonus


def _technique_coef(char_id: int) -> Tuple[int, int]:
    """取已装备心法的修炼系数（bp）与最高层数。

    心法最多 1 部；未装备时退化为已学心法中最高的那部。
    """
    rows = TechniqueRepository.list_for(char_id)
    hearts = []
    for row in rows:
        try:
            tech = get_registry().technique(str(row["tech_id"]))
        except Exception:
            continue
        if str(tech.get("type")) == "heart":
            hearts.append((row, tech))
    if not hearts:
        return 10000, 0
    equipped = [x for x in hearts if str(x[0].get("equipped_slot") or "") == "heart"]
    row, tech = (equipped or hearts)[0]
    coef = int(tech.get("cultivation_multiplier_bp", 10000))
    return coef, int(row.get("layer", 1))


def _direction_coef(char: Mapping[str, Any]) -> float:
    """方向系数：适配 1.3 / 中性 1.0 / 不适 0.8；无方向与散修恒 1.0。"""
    direction = str(char.get("direction_id") or "")
    if not direction or str(char.get("path_id") or "") == "sansxiu":
        return 1.0
    try:
        physique = get_registry().physique(str(char.get("physique") or "fan_ti"))
    except Exception:
        return 1.0
    return float(dict(physique.get("affinity") or {}).get(direction, 1.0))


def _skills_of(char_id: int) -> List[Dict[str, Any]]:
    """已装备攻击功法 → 战斗技能。"""
    out: List[Dict[str, Any]] = []
    for row in TechniqueRepository.list_for(char_id):
        slot = str(row.get("equipped_slot") or "")
        if not slot.startswith("atk"):
            continue
        try:
            tech = get_registry().technique(str(row["tech_id"]))
        except Exception:
            continue
        skill = dict(tech.get("skill") or {})
        power = int(skill.get("power_pct", 0) or 0)
        if power <= 0:
            continue
        layer = int(row.get("layer", 1))
        out.append({
            "id": str(tech["id"]),
            "name": str(tech["name"]),
            "power_pct": int(power * (1.0 + 0.05 * max(0, layer - 1))),
            "mp_cost": int(skill.get("mp_cost", 0)),
            "cooldown": int(skill.get("cooldown", 0)),
            "effect": str(skill.get("effect", "")),
            "stun_bp": 3000 if ("麻痹" in str(skill.get("effect", "")) or "昏迷" in str(skill.get("effect", ""))) else 0,
        })
    return out


def build(char_or_id: Any) -> CharacterStats:
    """计算角色面板。

    Args:
        char_or_id: 角色行 dict 或 character_id。

    Returns:
        ``CharacterStats``。

    Raises:
        GameError: 4003 角色不存在。
    """
    char = (CharacterRepository.require(int(char_or_id))
            if not isinstance(char_or_id, Mapping) else dict(char_or_id))
    registry = get_registry()
    base = {key: int(char.get(key, 0)) for key in attrs_domain.BASE_KEYS}
    # mnd / karma 不是 character 的同名列：道心、因果是特殊轴
    base["mnd"] = int(char.get("dao_heart", 10))
    base["karma"] = int(char.get("karma", 0))
    # 因果是特殊轴，不参与装备百分比
    equip_bonus, base_pct, base_flat, bag_bonus = _equip_bonus(int(char["id"]))
    dir_bonus, dir_pct = _direction_bonus(char)
    for key, value in dir_bonus.items():
        equip_bonus[key] = equip_bonus.get(key, 0.0) + value
    for key, value in dir_pct.items():
        base_pct[key] = base_pct.get(key, 0) + value
    for key, value in base_flat.items():
        if key in base and key not in ("karma",):
            base[key] = int(base[key]) + int(value)
    for key, pct in base_pct.items():
        if key in base and key not in ("karma",):
            base[key] = int(base[key] * (100 + pct) / 100)
    caps = registry.attrs_config().get("soft_cap", {})
    base = attrs_domain.apply_soft_caps(base, caps)

    realm = registry.realm_by_index(int(char["realm_index"]))
    power_coef = float(realm.get("power_coef", 1.0))
    derived = attrs_domain.compute_derived(base, power_coef, equip_bonus)

    hp = int(char.get("hp") or 0) or derived.hp_max
    mp = int(char.get("mp") or 0) or derived.mp_max
    hp = max(0, min(int(hp), derived.hp_max))
    mp = max(0, min(int(mp), derived.mp_max))

    tech_coef_bp, heart_layer = _technique_coef(int(char["id"]))
    physique = registry.physique(str(char.get("physique") or "fan_ti"))
    env = 1.0
    try:
        env = float(registry.region(str(char.get("region_id") or "duanhuai_village"))
                    .get("env_coef", 1.0))
    except Exception:
        env = 1.0
    mood_bp = registry.mood_coef_bp(int(char.get("dao_heart", 10)))
    status_bp = registry.status_coef_bp(str(char.get("status") or "normal"))
    direction = _direction_coef(char)
    global_bp = int(registry.exp_config().get("exp_rate_global_bp", 15000))

    breakdown = attrs_domain.exp_rate_breakdown(
        int(base.get("wis", 10)), int(char["realm_index"]), 30,
        global_bp=global_bp, tech_coef_bp=tech_coef_bp,
        physique_bp=int(physique.get("cultivation_bp", 10000)),
        env_coef=env, mood_bp=mood_bp, status_bp=status_bp,
        direction_coef=direction).as_dict()

    return CharacterStats(
        char=char,
        base=base,
        equip=equip_bonus,
        derived=derived,
        hp=hp,
        mp=mp,
        exp_rate=float(breakdown["rate"]),
        tech_coef_bp=tech_coef_bp,
        exp_breakdown=breakdown,
        skills=_skills_of(int(char["id"])),
        bag_slots=int(char.get("bag_slots", 60)) + bag_bonus,
    )


def exp_gain(char_id: int, days: int) -> Tuple[int, CharacterStats]:
    """计算一次闭关的修为产出（不落库）。"""
    stats = build(char_id)
    gain = attrs_domain.cultivation_gain(
        int(stats.base.get("wis", 10)), int(stats.char["realm_index"]), int(days),
        global_bp=int(get_registry().exp_config().get("exp_rate_global_bp", 15000)),
        tech_coef_bp=stats.tech_coef_bp,
        physique_bp=int(get_registry().physique(str(stats.char["physique"]))
                        .get("cultivation_bp", 10000)),
        env_coef=float(stats.exp_breakdown["env"]),
        mood_bp=get_registry().mood_coef_bp(int(stats.char.get("dao_heart", 10))),
        status_bp=get_registry().status_coef_bp(str(stats.char.get("status") or "normal")),
        direction_coef=_direction_coef(stats.char))
    return int(gain), stats
