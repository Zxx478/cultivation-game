"""domain.realm：成功率明细、准备上限、连败补偿、保底、惩罚、根基、前置校验。"""
from __future__ import annotations

import pytest

from app.core.errors import (
    BREAKTHROUGH_COOLDOWN,
    BREAKTHROUGH_LIFESPAN_LOW,
    CULTIVATION_INSUFFICIENT,
    GameError,
)
from app.domain import level, realm


def test_preview_items_and_sum():
    """成功率 = 基础 + 体质 + 根基 + 准备 + 连败补偿，逐项可查。"""
    preview = realm.preview_breakthrough(
        8500, physique_bp=300, foundation_bp=500,
        pill_count=1, guardian=True, omen=True, land=True, fail_streak=1)
    keys = [item.key for item in preview.items]
    assert keys == ["base", "physique", "foundation", "pill",
                    "guardian", "omen", "land", "fail_bonus"]
    assert preview.prepare_bp == 800 + 1000 + 600 + 600
    assert preview.final_bp == 9500  # clamp 到上限
    assert preview.guaranteed is False


def test_prepare_bonus_capped_at_3000():
    """四类准备合计上限 +30%（红线 5）。"""
    preview = realm.preview_breakthrough(
        5000, pill_count=2, guardian=True, omen=True, land=True)
    assert preview.prepare_bp == 3000
    assert preview.prepare_capped is True
    values = {item.key: item.value_bp for item in preview.items}
    assert values["pill"] == 1600          # 2 颗 × 800
    assert values["guardian"] == 1000


def test_pill_count_beyond_max_ignored():
    """破境丹最多计 2 颗。"""
    preview = realm.preview_breakthrough(5000, pill_count=5)
    assert {i.key: i.value_bp for i in preview.items}["pill"] == 1600


def test_fail_bonus_step_and_cap():
    """连败补偿 +15%/次，上限 +30%。"""
    assert {i.key: i.value_bp for i in realm.preview_breakthrough(5000, fail_streak=1).items}["fail_bonus"] == 1500
    assert {i.key: i.value_bp for i in realm.preview_breakthrough(5000, fail_streak=2).items}["fail_bonus"] == 3000
    assert {i.key: i.value_bp for i in realm.preview_breakthrough(5000, fail_streak=4).items}["fail_bonus"] == 3000


def test_guaranteed_after_five_fails():
    """5 连败后第 6 次必成：final_bp=10000 且 guaranteed=True（红线 5）。"""
    preview = realm.preview_breakthrough(3000, fail_streak=5)
    assert preview.final_bp == 10000
    assert preview.guaranteed is True
    assert preview.as_dict()["guaranteed"] is True


def test_clamp_bounds():
    """成功率恒在 5%–95% 之间。"""
    assert realm.preview_breakthrough(100).final_bp == 500
    assert realm.preview_breakthrough(12000).final_bp == 9500


def test_penalty_tiers():
    """三档惩罚：≥60% 轻伤 / 30–60% 中伤 / <30% 重伤（含道心与走火）。"""
    light = realm.penalty_of(7000)
    medium = realm.penalty_of(4000)
    heavy = realm.penalty_of(1000)
    assert (light["name"], light["cultivation_loss_bp"], light["dao_heart"]) == ("light", 1000, 0)
    assert (medium["name"], medium["cultivation_loss_bp"]) == ("medium", 2000)
    assert heavy["name"] == "heavy"
    assert heavy["dao_heart"] == 5
    assert heavy["qi_deviation_bp"] == 500


def test_penalty_application_helpers():
    """修为/气血扣减不为负，且至少保留 1 点气血。"""
    assert realm.punish_cultivation(1000, 1000) == 650
    assert realm.punish_cultivation(100, 7000) == 90
    assert realm.punish_hp(1000, 1000) == 100
    assert realm.punish_hp(10, 1000) == 1


def test_foundation_by_attempts():
    """根基：1 圆满(+5%) / 2 扎实(+2%) / 3 虚浮(−3%) / ≥4 崩坏(−8%)。"""
    assert realm.foundation_of(1)["name"] == "圆满"
    assert realm.foundation_bp(1) == 500
    assert realm.foundation_of(2)["attr_bonus_bp"] == 200
    assert realm.foundation_of(3)["attr_bonus_bp"] == -300
    assert realm.foundation_of(9)["name"] == "崩坏"
    assert realm.foundation_of(9)["next_realm_bp"] == -500


def test_lifespan_recompute_is_absolute():
    """寿元整体重算（不是加差值）：锻血 100 年 → 辟府 150 年。"""
    assert realm.lifespan_max_days(100) == 36000
    assert realm.lifespan_max_days(150) == 54000
    assert realm.lifespan_max_days(150, extra_years=0.5) == 54180
    assert realm.lifespan_left_days(1000, 36000) == 35000


def test_prereq_level_and_cultivation():
    """等级不满/修为不满抛 4007。"""
    with pytest.raises(GameError) as exc:
        realm.check_prereq(level=8, realm_level_end=9, cultivation=99999,
                           exp_k_bp=10000, age_days=100,
                           lifespan_max_days_value=36000)
    assert exc.value.code == CULTIVATION_INSUFFICIENT
    with pytest.raises(GameError) as exc2:
        realm.check_prereq(level=9, realm_level_end=9, cultivation=1,
                           exp_k_bp=10000, age_days=100,
                           lifespan_max_days_value=36000)
    assert exc2.value.code == CULTIVATION_INSUFFICIENT


def test_prereq_lifespan_min_ten_years():
    """寿元 < 10 年禁止突破（红线 6）。"""
    need = level.exp_need(9, 10000)
    with pytest.raises(GameError) as exc:
        realm.check_prereq(level=9, realm_level_end=9, cultivation=need,
                           exp_k_bp=10000, age_days=36000 - 3599,
                           lifespan_max_days_value=36000)
    assert exc.value.code == BREAKTHROUGH_LIFESPAN_LOW


def test_prereq_cooldown():
    """失败后 30 日内不得再冲关（4009），第 31 日放行。"""
    need = level.exp_need(9, 10000)
    with pytest.raises(GameError) as exc:
        realm.check_prereq(level=9, realm_level_end=9, cultivation=need,
                           exp_k_bp=10000, age_days=1000,
                           lifespan_max_days_value=36000,
                           last_fail_age_days=980)
    assert exc.value.code == BREAKTHROUGH_COOLDOWN
    # 冷却已过：不抛异常
    assert realm.check_prereq(level=9, realm_level_end=9, cultivation=need,
                              exp_k_bp=10000, age_days=1010,
                              lifespan_max_days_value=36000,
                              last_fail_age_days=980) is None
