"""domain.attrs：10 基础 + 13 衍生 + CP + 软上限 + ExpRate 七层乘算。"""
from __future__ import annotations

import math

import pytest

from app.domain import attrs

BASE = {
    "str": 10, "con": 10, "spr": 10, "sen": 10, "bon": 10,
    "wis": 30, "agi": 10, "luk": 5, "mnd": 10, "karma": 0,
}


def test_hp_mp_formula_matches_prd():
    """HP = 50 + 体魄×12 + √战力×80；MP = 30 + 灵力×10 + √战力×60。"""
    derived = attrs.compute_derived(BASE, 1.0)
    assert derived.hp_max == 50 + 10 * 12 + 80
    assert derived.mp_max == 30 + 10 * 10 + 60
    # 辟府战力系数 3.0 → √3
    derived2 = attrs.compute_derived(BASE, 3.0)
    assert derived2.hp_max == int(50 + 120 + math.sqrt(3) * 80)
    assert derived2.mp_max == int(30 + 100 + math.sqrt(3) * 60)


def test_atk_def_pen_from_base():
    """ATK/DEF/PEN 线性公式，装备加成直接叠加。"""
    derived = attrs.compute_derived(BASE, 1.0)
    assert derived.atk == pytest.approx(10 * 2.2)
    assert derived.defense == pytest.approx(10 * 1.2)
    assert derived.pen == pytest.approx(10 * 1.5 + 10 * 0.5)
    with_equip = attrs.compute_derived(BASE, 1.0, {"atk": 40, "def": 28, "hp_flat": 100})
    assert with_equip.atk == pytest.approx(derived.atk + 40)
    assert with_equip.defense == pytest.approx(derived.defense + 28)
    assert with_equip.hp_max == derived.hp_max + 100


def test_dr_capped_at_75_percent():
    """减伤率上限 75%，且分母 = 500 + √战力×300。"""
    denom = attrs.dr_denominator(1.0)
    assert denom == pytest.approx(800.0)
    crazy = attrs.compute_derived({**BASE, "con": 9999}, 12.0, {"def": 99999})
    assert crazy.dr_bp == 7500
    zero = attrs.compute_derived({**BASE, "con": 0}, 1.0)
    assert zero.dr_bp == 0


def test_secondary_caps():
    """暴击 60% / 暴伤 300% / 命中 99% / 闪避 40% 上限。"""
    strong = attrs.compute_derived({**BASE, "sen": 9999, "agi": 9999, "wis": 9999}, 1.0)
    assert strong.crit_bp == 6000
    assert strong.crit_dmg_bp == 30000
    assert strong.hit_bp == 9900
    assert strong.evade_bp == 4000


def test_init_reveal_mr_and_cp():
    """先手/探查/道心抗性与战力 CP。"""
    derived = attrs.compute_derived(BASE, 1.0)
    assert derived.init == pytest.approx(10 * 1.5 + 10 * 0.5)
    assert derived.reveal == pytest.approx(1.0)
    assert derived.mr == pytest.approx(15.0)
    assert derived.cp > 0
    assert attrs.total_cp(derived, equipment_cp=10) == derived.cp + 10


def test_soft_caps_including_karma_both_ways():
    """软上限：常规上限 + 因果双向上限。"""
    caps = {"str": 9999, "bon": 999, "wis": 500, "karma_max": 1000, "karma_min": -1000}
    assert attrs.apply_soft_cap("wis", 999, caps) == 500
    assert attrs.apply_soft_cap("karma", 5000, caps) == 1000
    assert attrs.apply_soft_cap("karma", -5000, caps) == -1000
    assert attrs.apply_soft_cap("str", -5, caps) == 0
    capped = attrs.apply_soft_caps({"str": 20000, "karma": -2000}, caps)
    assert capped["str"] == 9999 and capped["karma"] == -1000


def test_exp_rate_matches_design_sample():
    """悟性 30、锻血、全中性 → 43.5 修为/日；闭关 30 日 = 1,305（D4 口径）。"""
    assert attrs.exp_rate(30, 1) == pytest.approx(43.5)
    assert attrs.cultivation_gain(30, 1, 30) == 1305


def test_exp_rate_seven_layers_multiply():
    """七层乘算：环境 1.5 / 方向 1.3 / 心境 1.2 / 状态 0.8 / 功法 1.36 逐层生效。"""
    base = attrs.exp_rate_raw(30, 1)
    assert attrs.exp_rate_raw(30, 1, env_coef=1.5) == pytest.approx(base * 1.5)
    assert attrs.exp_rate_raw(30, 1, direction_coef=1.3) == pytest.approx(base * 1.3)
    assert attrs.exp_rate_raw(30, 1, mood_bp=12000) == pytest.approx(base * 1.2)
    assert attrs.exp_rate_raw(30, 1, status_bp=8000) == pytest.approx(base * 0.8)
    assert attrs.exp_rate_raw(30, 1, tech_coef_bp=13600) == pytest.approx(base * 1.36)
    # 境界成长：辟府 ×1.35
    assert attrs.exp_rate_raw(30, 2) == pytest.approx(base * 1.35)
    # 散修方向系数恒 1.0，不得有额外加成
    assert attrs.exp_rate_raw(30, 1, direction_coef=1.0) == pytest.approx(base)


def test_exp_rate_floors_only_once():
    """浮点累计后只 floor 一次，不做逐层取整。"""
    rate = attrs.exp_rate(30, 1, env_coef=1.5, direction_coef=1.3,
                          tech_coef_bp=13600, mood_bp=11000)
    gain = attrs.cultivation_gain(30, 1, 15, env_coef=1.5, direction_coef=1.3,
                                  tech_coef_bp=13600, mood_bp=11000)
    assert gain == int(math.floor(rate * 15))
    breakdown = attrs.exp_rate_breakdown(30, 1, 30)
    assert breakdown.gain_per_days == 1305
    assert breakdown.global_bp == 15000
