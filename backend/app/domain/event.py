"""事件：候选筛选（区域 × 境界 × 30 月去重）、判定、效果 DSL。

判定公式（PRD §6.4 / QA R73）::

    成功率 = clamp( 选项基础值(40%–70%)
                    + 属性修正（按 check.attr）
                    + 性格修正(±8% / −5%)
                    + 气运 × 0.3%
                    + 因果修正(>0 时 +因果/100；<0 时 −|因果|/150)
                  , 5%, 95% )

- 同一事件在 ``dedupe_days``（30 游戏月 = 1800 日）内不再抽中；
- 抽签顺序：类别权重（game.yaml ``category_weights_bp``）× 事件 ``weight``；
- 效果 DSL 见 ``apply_effects``，只产出「增量」，落库由 service 负责。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core.rng import RngService

DEFAULT_EVENT_CFG: Dict[str, Any] = {
    "trigger_bp": 3500,
    "sansxiu_event_multiplier_bp": 11000,
    "dedupe_days": 1800,
    "dedupe_months": 30,
    "base_success_bp": 5500,
    "success_min_bp": 500,
    "success_max_bp": 9500,
    "luck_factor_bp": 30,
    "karma_positive_divisor": 100,
    "karma_negative_divisor": 150,
    "max_time_cost_days": 720,
}

# 属性 → 每点提供的判定加成（bp）。口径见 PRD §6.4「典型属性修正」：
# 硬闯 力量×0.05%（5bp/点）、体魄×0.03%（3bp/点）；
# 智取 神识×0.06%（6bp/点）、悟性×0.04%（4bp/点）；
# 交涉 气运×0.5%（50bp/点，此处按 5bp/点保守取整，气运另有独立通道）。
ATTR_BONUS_BP: Dict[str, int] = {
    "str": 5, "con": 3, "spr": 4, "sen": 6, "wis": 4,
    "agi": 5, "bon": 3, "mnd": 5, "luk": 5, "karma": 0,
}

EFFECT_TYPES = {
    "cultivation", "spirit_stone", "item", "item_loss", "karma", "hp_pct",
    "mp_pct", "attr", "dao_heart", "status", "favor", "technique", "flag", "buff",
}


def _cfg(overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """合并外部事件配置与缺省。"""
    out = dict(DEFAULT_EVENT_CFG)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


# ---------------- 抽签 ----------------

def is_available(event: Mapping[str, Any], *, region_id: str, level: int) -> bool:
    """区域 + 等级是否匹配（不含去重）。"""
    if region_id not in event.get("region_ids", ()):
        return False
    return int(event["level_min"]) <= int(level) <= int(event["level_max"])


def candidates(events: Sequence[Mapping[str, Any]], *, region_id: str, level: int,
               now_days: int, last_seen: Optional[Mapping[str, Any]] = None,
               cfg: Optional[Mapping[str, Any]] = None,
               include_forced: bool = False) -> List[Mapping[str, Any]]:
    """筛出可用事件（区域 × 等级 × 30 月去重）。

    Args:
        events: 事件序列（``registry.events_for`` 或全量）。
        region_id: 当前区域。
        level: 当前全局等级。
        now_days: 当前游戏日（``age_days``）。
        last_seen: ``{event_id: 上次触发时的 age_days}``（来自 character_flag）。
        cfg: 事件配置。
        include_forced: 是否包含主线强制事件（默认排除）。

    Returns:
        可用事件列表。
    """
    conf = _cfg(cfg)
    dedupe = int(conf["dedupe_days"])
    seen = last_seen or {}
    out: List[Mapping[str, Any]] = []
    for event in events:
        if event.get("forced_by_quest") and not include_forced:
            continue
        if not is_available(event, region_id=region_id, level=level):
            continue
        last = seen.get(str(event["id"]))
        if last is not None and int(now_days) - int(last) < dedupe:
            continue
        out.append(event)
    return out


def weight_pool(events: Sequence[Mapping[str, Any]],
                category_weight: Any) -> List[Tuple[Mapping[str, Any], int]]:
    """构造加权池：``(event, 类别权重 × 事件权重)``。

    Args:
        events: 候选事件。
        category_weight: 类别 → bp 的映射，或 ``callable(category) -> int``。
    """
    pool: List[Tuple[Mapping[str, Any], int]] = []
    for event in events:
        if callable(category_weight):
            cw = int(category_weight(str(event["category"])))
        else:
            cw = int(dict(category_weight).get(str(event["category"]), 0))
        w = cw * int(event.get("weight", 0))
        if w > 0:
            pool.append((event, w))
    return pool


def pick_event(pool: Sequence[Tuple[Mapping[str, Any], int]],
               rng: RngService, *, stream: str = "event:pick") -> Optional[Mapping[str, Any]]:
    """从加权池抽一条；池空返回 ``None``（保底不空转）。"""
    if not pool:
        return None
    return rng.weighted_pick([(item[0], item[1]) for item in pool], stream=stream)


def trigger_target_bp(path_id: str, cfg: Optional[Mapping[str, Any]] = None) -> int:
    """事件触发阈值（含散修相对加成，**乘法**）。

    ``sansxiu``：3500 × 1.10 = 3850bp（不是加法 4500）。
    """
    conf = _cfg(cfg)
    base = int(conf["trigger_bp"])
    if path_id == "sansxiu":
        return int(base * int(conf["sansxiu_event_multiplier_bp"]) // 10000)
    return base


def roll_trigger(path_id: str, rng: RngService, *,
                 stream: str = "event:trigger",
                 cfg: Optional[Mapping[str, Any]] = None) -> bool:
    """判定本次行动是否触发事件。"""
    return rng.roll_bp(trigger_target_bp(path_id, cfg), stream=stream)


# ---------------- 判定 ----------------

def success_bp(choice: Mapping[str, Any], *, attrs: Mapping[str, Any],
               personality_mod_bp: int = 0, luck: Optional[int] = None,
               karma: int = 0,
               cfg: Optional[Mapping[str, Any]] = None) -> int:
    """计算某选项的成功率（bp，已 clamp 5%–95%）。

    Args:
        choice: 选项配置（``check`` 可为 None，表示必成）。
        attrs: 角色基础属性。
        personality_mod_bp: 性格修正（±800 / −500）。
        luck: 气运（None 时取 ``attrs['luk']``）。
        karma: 因果值。
        cfg: 事件配置。

    Returns:
        成功率 bp；``check`` 为 None 时返回 10000。
    """
    conf = _cfg(cfg)
    check = choice.get("check")
    if not check:
        return 10000

    base = int(check.get("difficulty_bp", conf["base_success_bp"]))
    attr_key = str(check.get("attr", "wis"))
    attr_bonus = int(attrs.get(attr_key, 0)) * ATTR_BONUS_BP.get(attr_key, 0)
    luck_value = int(attrs.get("luk", 0) if luck is None else luck)
    luck_bonus = luck_value * int(conf["luck_factor_bp"])

    if karma > 0:
        karma_bonus = int(karma * 100 / int(conf["karma_positive_divisor"]))
    elif karma < 0:
        karma_bonus = -int((-karma) * 100 / int(conf["karma_negative_divisor"]))
    else:
        karma_bonus = 0

    raw = base + attr_bonus + int(personality_mod_bp) + luck_bonus + karma_bonus
    lo, hi = int(conf["success_min_bp"]), int(conf["success_max_bp"])
    return max(lo, min(hi, raw))


def resolve(choice: Mapping[str, Any], chance_bp: int, rng: RngService, *,
            stream: str = "event:roll") -> bool:
    """掷骰判定成功与否（``check`` 为空直接成功）。"""
    if not choice.get("check"):
        return True
    return rng.roll_bp(int(chance_bp), stream=stream)


# ---------------- 效果 DSL ----------------

@dataclass
class EffectOutcome:
    """一组效果的增量集合（**不落地**，由 service 合并进角色状态）。"""

    attr: Dict[str, int] = field(default_factory=dict)
    cultivation: int = 0
    spirit_stone: int = 0
    karma: int = 0
    dao_heart: int = 0
    hp_pct: int = 0
    mp_pct: int = 0
    items_gained: Dict[str, int] = field(default_factory=dict)
    items_lost: Dict[str, int] = field(default_factory=dict)
    techniques: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    favors: Dict[str, int] = field(default_factory=dict)
    buffs: List[Dict[str, Any]] = field(default_factory=list)
    unhandled: List[Dict[str, Any]] = field(default_factory=list)

    def merge(self, other: "EffectOutcome") -> "EffectOutcome":
        """合并另一组增量（返回自身，便于链式）。"""
        for key, value in other.attr.items():
            self.attr[key] = self.attr.get(key, 0) + value
        self.cultivation += other.cultivation
        self.spirit_stone += other.spirit_stone
        self.karma += other.karma
        self.dao_heart += other.dao_heart
        self.hp_pct += other.hp_pct
        self.mp_pct += other.mp_pct
        for key, value in other.items_gained.items():
            self.items_gained[key] = self.items_gained.get(key, 0) + value
        for key, value in other.items_lost.items():
            self.items_lost[key] = self.items_lost.get(key, 0) + value
        self.techniques.extend(other.techniques)
        self.flags.extend(other.flags)
        self.statuses.extend(other.statuses)
        for key, value in other.favors.items():
            self.favors[key] = self.favors.get(key, 0) + value
        self.buffs.extend(other.buffs)
        self.unhandled.extend(other.unhandled)
        return self

    def as_dict(self) -> Dict[str, Any]:
        """转字典（写日志/回包用）。"""
        return asdict(self)


def apply_effects(effects: Iterable[Mapping[str, Any]]) -> EffectOutcome:
    """把效果数组折成增量集合。

    支持的 ``type``（见 events.yaml 顶注释）：
    ``cultivation / spirit_stone / item / item_loss / karma / hp_pct /
    mp_pct / attr / dao_heart / status / favor / technique / flag / buff``。
    未知类型写入 ``unhandled``，不抛异常（内容新增效果时旧引擎不崩）。

    Args:
        effects: ``success_effects`` / ``fail_effects`` 列表。

    Returns:
        ``EffectOutcome``。
    """
    out = EffectOutcome()
    for effect in effects or ():
        etype = str(effect.get("type", ""))
        value = int(effect.get("value", 0))
        if etype == "cultivation":
            out.cultivation += value
        elif etype == "spirit_stone":
            out.spirit_stone += value
        elif etype == "karma":
            out.karma += value
        elif etype == "dao_heart":
            out.dao_heart += value
        elif etype == "hp_pct":
            out.hp_pct += value
        elif etype == "mp_pct":
            out.mp_pct += value
        elif etype == "attr":
            key = str(effect.get("key", ""))
            out.attr[key] = out.attr.get(key, 0) + value
        elif etype == "item":
            item_id = str(effect.get("item_id", ""))
            qty = int(effect.get("qty", 1))
            out.items_gained[item_id] = out.items_gained.get(item_id, 0) + qty
        elif etype == "item_loss":
            item_id = str(effect.get("item_id", ""))
            qty = int(effect.get("qty", 1))
            out.items_lost[item_id] = out.items_lost.get(item_id, 0) + qty
        elif etype == "technique":
            out.techniques.append(str(effect.get("tech_id", "")))
        elif etype == "flag":
            out.flags.append(str(effect.get("key", "")))
        elif etype == "status":
            out.statuses.append(str(effect.get("key", "")))
        elif etype == "favor":
            npc_id = str(effect.get("npc_id", ""))
            out.favors[npc_id] = out.favors.get(npc_id, 0) + value
        elif etype == "buff":
            out.buffs.append({
                "key": str(effect.get("key", "")),
                "value": value,
                "duration_days": int(effect.get("duration_days", 0)),
            })
        else:
            out.unhandled.append(dict(effect))
    return out


def resolve_choice(choice: Mapping[str, Any], *, attrs: Mapping[str, Any],
                   personality_mod_bp: int = 0, karma: int = 0,
                   rng: RngService, institute_stream: str = "event:choose",
                   cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """一站式结算：算成功率 → 掷骰 → 取对应效果。

    Returns:
        ``{success, chance_bp, effects, time_cost_days, choice_id}``。
    """
    conf = _cfg(cfg)
    chance = success_bp(choice, attrs=attrs, personality_mod_bp=personality_mod_bp,
                        karma=karma, cfg=conf)
    ok = resolve(choice, chance, rng, stream=f"{institute_stream}:{choice.get('id', '')}")
    branch = (choice.get("success_effects") if ok else choice.get("fail_effects")) or ()
    return {
        "success": bool(ok),
        "chance_bp": int(chance),
        "choice_id": str(choice.get("id", "")),
        "time_cost_days": min(int(choice.get("time_cost_days", 0)),
                              int(conf["max_time_cost_days"])),
        "effects": apply_effects(branch).as_dict(),
    }


def find_choice(event: Mapping[str, Any], option_id: str) -> Mapping[str, Any]:
    """按 id 取选项（找不到抛 KeyError，由 service 转 4011）。"""
    for choice in event["choices"]:
        if str(choice.get("id")) == str(option_id):
            return choice
    raise KeyError(option_id)
