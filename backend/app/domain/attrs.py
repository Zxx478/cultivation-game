"""属性与战力：10 基础 + 13 衍生 + CP + 软上限 + ExpRate 七层乘算。

公式严格取自 PRD §4.1 / §4.2 与任务书 §8：

============  ===============================================  ==============
衍生属性       公式                                              上限
============  ===============================================  ==============
气血 HP        ``50 + 体魄×12 + √战力系数×80``                   —
灵力 MP        ``30 + 灵力×10 + √战力系数×60``                   —
攻击 ATK       ``力量×2.2 + 武器攻击``                            —
防御 DEF       ``体魄×1.2 + 护甲防御``                            —
减伤 DR        ``DEF / (DEF + 500 + √战力系数×300)``              75%
破防 PEN       ``力量×1.5 + 神识×0.5``                            —
暴击率 CR      ``5% + 神识×0.03% + 身法×0.02%``                   60%
暴击伤害 CD    ``150% + 悟性×0.2%``                               300%
命中 HIT       ``85% + 神识×0.03%``                               99%
闪避 EVA       ``3% + 身法×0.04%``                                40%
先手 INIT      ``身法×1.5 + 神识×0.5``                            —
神识探查       ``神识/10`` 米                                     —
道心抗性 MR    ``道心×1.5 + 心境加成``                            —
============  ===============================================  ==============

数值口径：内部一律用 ``float`` 计算，**只在最终产出时 floor 一次**，
避免"层层取整"造成的 1 点误差累积（QA R29–R32）。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional

# 10 项基础属性键（顺序即 UI 顺序）
BASE_KEYS = ("str", "con", "spr", "sen", "bon", "wis", "agi", "luk", "mnd", "karma")

# 软上限缺省（game.yaml.attrs.soft_cap 优先）
DEFAULT_SOFT_CAP: Dict[str, int] = {
    "str": 9999, "con": 9999, "spr": 9999, "sen": 9999, "bon": 999,
    "wis": 500, "agi": 9999, "luk": 100, "mnd": 100,
}


@dataclass(frozen=True)
class DerivedAttrs:
    """13 项衍生属性。

    百分比类字段单位为 **bp**（10000 = 100%），便于与其他模块统一；
    整数类字段为绝对值。
    """

    hp_max: int
    mp_max: int
    atk: float
    defense: float
    dr_bp: int
    pen: float
    crit_bp: int
    crit_dmg_bp: int
    hit_bp: int
    evade_bp: int
    init: float
    reveal: float
    mr: float
    cp: int
    dr_denom: float

    def as_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的字典。"""
        return asdict(self)


def dr_denominator(power_coef: float) -> float:
    """减伤分母常数 ``500 + √战力系数×300``（战斗内防御指令复用）。"""
    return 500.0 + math.sqrt(max(power_coef, 0.0)) * 300.0


def dr_bp_from_def(defense: float, denom: float, *, cap_bp: int = 7500) -> int:
    """由防御值算减伤（bp），并夹到上限。"""
    if defense <= 0:
        return 0
    raw = defense / (defense + max(denom, 1.0))
    return int(min(cap_bp, round(raw * 10000)))


def compute_derived(base: Mapping[str, Any], power_coef: float,
                    equip: Optional[Mapping[str, Any]] = None) -> DerivedAttrs:
    """计算 13 项衍生属性与战力。

    Args:
        base: 10 项基础属性（缺省按 0 处理；未知键忽略）。
        power_coef: 境界战力系数（realms.yaml.power_coef）。
        equip: 装备加成，可含 ``atk`` / ``def`` / ``hp_flat`` / ``hp_pct`` /
            ``mp_flat`` / ``mp_pct`` / ``pen`` / ``crit_bp`` / ``crit_dmg_bp``
            / ``hit_bp`` / ``evade_bp`` / ``init``（百分比为 bp）。

    Returns:
        ``DerivedAttrs``。
    """
    eq = dict(equip or {})
    strength = float(base.get("str", 0))
    con = float(base.get("con", 0))
    spr = float(base.get("spr", 0))
    sen = float(base.get("sen", 0))
    wis = float(base.get("wis", 0))
    agi = float(base.get("agi", 0))
    mnd = float(base.get("mnd", 10))

    root = math.sqrt(max(power_coef, 0.0))

    hp_flat = float(eq.get("hp_flat", 0))
    hp_max_raw = (50.0 + con * 12.0 + root * 80.0 + hp_flat) * (1.0 + eq.get("hp_pct", 0) / 10000.0)
    mp_flat = float(eq.get("mp_flat", 0))
    mp_max_raw = (30.0 + spr * 10.0 + root * 60.0 + mp_flat) * (1.0 + eq.get("mp_pct", 0) / 10000.0)

    atk = strength * 2.2 + float(eq.get("atk", 0))
    defense = con * 1.2 + float(eq.get("def", 0))
    denom = dr_denominator(power_coef)
    dr_bp = dr_bp_from_def(defense, denom)

    pen = strength * 1.5 + sen * 0.5 + float(eq.get("pen", 0))
    crit_bp = int(min(6000, round((0.05 + sen * 0.0003 + agi * 0.0002) * 10000) + eq.get("crit_bp", 0)))
    crit_dmg_bp = int(min(30000, round((1.5 + wis * 0.002) * 10000) + eq.get("crit_dmg_bp", 0)))
    hit_bp = int(min(9900, round((0.85 + sen * 0.0003) * 10000) + eq.get("hit_bp", 0)))
    evade_bp = int(min(4000, round((0.03 + agi * 0.0004) * 10000) + eq.get("evade_bp", 0)))
    init = agi * 1.5 + sen * 0.5 + float(eq.get("init", 0))
    reveal = sen / 10.0
    mr = mnd * 1.5

    hp_max = int(math.floor(hp_max_raw))
    mp_max = int(math.floor(mp_max_raw))
    cp = int(math.floor(atk * 2.0 + defense * 1.5 + hp_max * 0.2 + mp_max * 0.1
                        + pen * 0.8 + init * 0.6))

    return DerivedAttrs(
        hp_max=hp_max,
        mp_max=mp_max,
        atk=atk,
        defense=defense,
        dr_bp=dr_bp,
        pen=pen,
        crit_bp=crit_bp,
        crit_dmg_bp=crit_dmg_bp,
        hit_bp=hit_bp,
        evade_bp=evade_bp,
        init=init,
        reveal=reveal,
        mr=mr,
        cp=cp,
        dr_denom=denom,
    )


def apply_soft_cap(key: str, value: int,
                   soft_cap: Optional[Mapping[str, int]] = None) -> int:
    """把单属性夹到软上限（因果有正负双向上限）。

    Args:
        key: 属性键。
        value: 目标值。
        soft_cap: 上限表；None 用缺省。

    Returns:
        夹取后的值。
    """
    caps = dict(DEFAULT_SOFT_CAP if soft_cap is None else soft_cap)
    if key == "karma":
        high = int(caps.get("karma_max", 1000))
        low = int(caps.get("karma_min", -1000))
        return max(low, min(high, int(value)))
    cap = int(caps.get(key, 9999))
    return max(0, min(cap, int(value)))


def apply_soft_caps(base: Mapping[str, Any],
                    soft_cap: Optional[Mapping[str, int]] = None) -> Dict[str, int]:
    """整表夹取（因果允许负值）。"""
    return {key: apply_soft_cap(key, int(base.get(key, 0)), soft_cap) for key in BASE_KEYS}


def exp_rate_raw(wis: int, realm_index: int, *,
                 tech_coef_bp: int = 10000,
                 physique_bp: int = 10000,
                 env_coef: float = 1.0,
                 mood_bp: int = 10000,
                 status_bp: int = 10000,
                 direction_coef: float = 1.0) -> float:
    """ExpRate 的七层乘算原始值（未乘全局倍率），单位「修为/游戏日」。

    Args:
        wis: 悟性。
        realm_index: 大境序号（1–12）。
        tech_coef_bp: 心法修炼系数（bp）。
        physique_bp: 体质修炼系数（bp）。
        env_coef: 区域环境系数（1.0/1.5/2.0/3.0/5.0）。
        mood_bp: 心境系数（bp，按道心）。
        status_bp: 状态系数（bp）。
        direction_coef: 方向系数（1.3/1.0/0.8；散修恒 1.0）。

    Returns:
        float（**未取整**，最终只 floor 一次）。
    """
    if realm_index < 1:
        raise ValueError("realm_index must be >= 1")
    base = 5.0 + wis * 0.8
    realm_growth = 1.0 + 0.35 * (realm_index - 1)
    return (base
            * realm_growth
            * (tech_coef_bp / 10000.0)
            * (physique_bp / 10000.0)
            * float(env_coef)
            * (mood_bp / 10000.0)
            * (status_bp / 10000.0)
            * float(direction_coef))


def exp_rate(wis: int, realm_index: int, *, global_bp: int = 15000,
             **kwargs: Any) -> float:
    """含全局倍率的 ExpRate（修为/游戏日）。"""
    return exp_rate_raw(wis, realm_index, **kwargs) * (global_bp / 10000.0)


def cultivation_gain(wis: int, realm_index: int, days: int, *,
                     global_bp: int = 15000, **kwargs: Any) -> int:
    """一次行动获得的修为（**只在此处 floor 一次**）。"""
    if days <= 0:
        raise ValueError("days must be > 0")
    return int(math.floor(exp_rate(wis, realm_index, global_bp=global_bp, **kwargs) * days))


@dataclass(frozen=True)
class ExpRateBreakdown:
    """ExpRate 七层明细（供突破/闭关界面展示 Δ）。"""

    base: float
    realm_growth: float
    tech_coef: float
    physique: float
    env: float
    mood: float
    status: float
    direction: float
    global_bp: int
    rate: float
    gain_per_days: int
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """转字典（含 extras）。"""
        data = asdict(self)
        data.update(self.extras)
        return data


def exp_rate_breakdown(wis: int, realm_index: int, days: int, *,
                       global_bp: int = 15000, **kwargs: Any) -> ExpRateBreakdown:
    """返回逐层系数与最终日产/总产，便于 UI 与回归测试。"""
    tech_bp = int(kwargs.get("tech_coef_bp", 10000))
    phys_bp = int(kwargs.get("physique_bp", 10000))
    env = float(kwargs.get("env_coef", 1.0))
    mood_bp = int(kwargs.get("mood_bp", 10000))
    status_bp = int(kwargs.get("status_bp", 10000))
    direction = float(kwargs.get("direction_coef", 1.0))
    raw = exp_rate_raw(wis, realm_index, **kwargs)
    rate = raw * (global_bp / 10000.0)
    return ExpRateBreakdown(
        base=5.0 + wis * 0.8,
        realm_growth=1.0 + 0.35 * (realm_index - 1),
        tech_coef=tech_bp / 10000.0,
        physique=phys_bp / 10000.0,
        env=env,
        mood=mood_bp / 10000.0,
        status=status_bp / 10000.0,
        direction=direction,
        global_bp=int(global_bp),
        rate=rate,
        gain_per_days=int(math.floor(rate * days)),
    )


def total_cp(derived: DerivedAttrs, *, equipment_cp: int = 0) -> int:
    """战力 CP（含装备附加值）。"""
    return int(derived.cp + equipment_cp)
