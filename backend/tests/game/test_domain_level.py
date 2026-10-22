"""domain.level：修为曲线、连升上限、溢出保留、封顶。"""
from __future__ import annotations

import pytest

from app.domain import level

# 锻血 K=1.0；辟府 K=1.8
K_FORGING = 10000
K_PALACE = 18000


def test_exp_need_key_nodes_match_prd():
    """PRD §3.2 关键节点必须逐条复现（QA 红线 2）。"""
    assert level.exp_need(1, K_FORGING) == 100
    assert level.exp_need(5, K_FORGING) == 1754
    assert level.exp_need(9, K_FORGING) == 4995
    assert level.exp_need(18, K_PALACE) == 30878


def test_exp_need_is_strictly_increasing():
    """曲线单调递增，且任意等级需求 > 0。"""
    prev = 0
    for lv in range(1, 100):
        k = K_FORGING if lv <= 9 else K_PALACE
        need = level.exp_need(lv, k)
        assert need > 0
        assert need > prev
        prev = need


def test_exp_need_rejects_bad_args():
    """非法参数直接报错，不做兜底。"""
    with pytest.raises(ValueError):
        level.exp_need(0, K_FORGING)
    with pytest.raises(ValueError):
        level.exp_need(3, 0)


def test_realm_index_and_stage():
    """每境 9 级：Lv.1–9 锻血、10–18 辟府、100 为第 12 境。"""
    assert level.realm_index_of(1) == 1
    assert level.realm_index_of(9) == 1
    assert level.realm_index_of(10) == 2
    assert level.realm_index_of(18) == 2
    assert level.realm_index_of(100) == 12
    assert level.realm_stage_of(1) == 1
    assert level.realm_stage_of(9) == 9
    assert level.realm_stage_of(10) == 1


def test_apply_exp_levels_up_once():
    """刚好够一级时只升一级，余量留在 cultivation。"""
    need = level.exp_need(1, K_FORGING)
    result = level.apply_exp(1, 0, 0, need + 7, K_FORGING)
    assert result.level == 2
    assert result.cultivation == 7
    assert result.levels_gained == 1
    assert result.exp_overflow == 0


def test_apply_exp_caps_at_five_levels_and_keeps_overflow():
    """单次最多连升 5 级；超出部分进溢出池且总量守恒（红线 3）。"""
    result = level.apply_exp(1, 0, 0, 100000, K_FORGING)
    assert result.levels_gained == 5
    assert result.level == 6
    assert result.overflow_added > 0
    assert result.exp_overflow == result.overflow_added
    need6 = level.exp_need(6, K_FORGING)
    assert result.cultivation == need6
    # 守恒：投入 = 已消耗升级需求 + cultivation + overflow
    consumed = sum(level.exp_need(lv, K_FORGING) for lv in range(1, 6))
    assert consumed + result.cultivation + result.exp_overflow == 100000
    # 溢出可在下一次结算立即续升
    nxt = level.apply_exp(result.level, result.cultivation, result.exp_overflow,
                          0, K_FORGING)
    assert nxt.level >= 7


def test_apply_exp_stops_at_cap_level():
    """Lv.18 封顶后只累积修为，不再升级（红线 2）。"""
    result = level.apply_exp(18, 0, 0, 10 ** 9, K_PALACE, cap_level=18)
    assert result.level == 18
    assert result.capped is True
    assert result.levels_gained == 0
    assert result.cultivation == 10 ** 9


def test_apply_exp_zero_gain_and_bad_input():
    """零收益不改等级；溢出池回填到当前修为（总量守恒）。"""
    result = level.apply_exp(3, 50, 10, 0, K_FORGING)
    assert result.level == 3
    assert result.cultivation + result.exp_overflow == 60
    assert (result.cultivation, result.exp_overflow) == (60, 0)
    with pytest.raises(ValueError):
        level.apply_exp(3, 0, 0, -1, K_FORGING)
    with pytest.raises(ValueError):
        level.apply_exp(19, 0, 0, 1, K_PALACE, cap_level=18)


def test_progress_and_full_flag():
    """修为条进度与「修为已满」判定。"""
    need = level.exp_need(4, K_FORGING)
    half = need // 2
    assert level.progress_bp(4, half, K_FORGING) == half * 10000 // need
    assert level.progress_bp(4, need, K_FORGING) == 10000
    assert level.progress_bp(4, 0, K_FORGING) == 0
    assert level.is_level_full(4, need, K_FORGING) is True
    assert level.is_level_full(4, need - 1, K_FORGING) is False
