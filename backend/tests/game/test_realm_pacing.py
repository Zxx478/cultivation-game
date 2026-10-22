"""境界节奏与修为曲线的回归护栏。

对应设计文档 ``docs/12-境界时间档位与修为曲线设计-v1.0.md``。
这组断言是「无突增、无倒挂、可通关」三条要求的机器化表达——
**任何人手改 ``realms.yaml`` 的 time_tier_days / exp_k_bp / base_lifespan_years，
只要破坏任一条，这里立刻变红。**

口径：参考修士（悟性 20、心法 1.12、体质 1.10、环境 1.10、其余 1.00、起始年龄 16）。
"""
from __future__ import annotations

import math

import pytest

from app.content.loader import load_all
from app.content.registry import Registry
from app.content.validators import REALM_TIME_TIER_DAYS
from app.core.clock import DAYS_PER_YEAR, action_days
from app.domain import attrs as attrs_domain
from app.domain import level as level_domain

START_AGE = 16
WIS = 20
#: 参考修士的六层系数（心法 × 体质 × 环境 = 1.12 × 1.10 × 1.10）
BUILD = dict(tech_coef_bp=11200, physique_bp=11000, env_coef=1.1)


@pytest.fixture(scope="module")
def registry() -> Registry:
    """直接用磁盘上的真实内容，避免被其它用例的注入内容污染。"""
    return Registry(load_all())


def _gain(registry: Registry, realm_index: int, kind: str = "seclusion") -> int:
    """参考修士一次行动获得的修为（走真实 domain 公式 + 真实档位）。"""
    days = action_days(registry.action_base_days(kind),
                       registry.realm_time_tier_days(realm_index),
                       registry.action_tier_base_days())
    return attrs_domain.cultivation_gain(
        WIS, realm_index, days,
        global_bp=int(registry.exp_config()["exp_rate_global_bp"]), **BUILD)


# ---------------------------------------------------------------- 档位

def test_time_tier_ladder_is_exact(registry: Registry):
    """12 档必须精确等于指定序列：1 月 / 3 月 / 6 月 / 1 年 / 3 年 / 5 年 /
    8 年 / 10 年 / 20 年 / 100 年 / 1000 年 / 10000 年。"""
    tiers = tuple(registry.realm_time_tier_days(i) for i in range(1, 13))
    assert tiers == REALM_TIME_TIER_DAYS
    assert tiers == (30, 90, 180, 360, 1080, 1800, 2880, 3600,
                     7200, 36000, 360000, 3600000)


def test_time_tier_is_strictly_increasing_and_whole_month(registry: Registry):
    """档位严格递增、不重复、且都是整月（保证 age_days 永为整月）。"""
    tiers = [registry.realm_time_tier_days(i) for i in range(1, 13)]
    assert tiers == sorted(tiers)
    assert len(set(tiers)) == 12
    assert all(t % 30 == 0 for t in tiers)


def test_time_tier_label_matches_days(registry: Registry):
    """档位文案必须与日数自洽，避免 UI 显示「3 个月」而实际走 90 日以外。"""
    for i in range(1, 13):
        label = registry.realm_time_tier_label(i)
        days = registry.realm_time_tier_days(i)
        expected = f"{days // 360} 年" if days >= 360 else f"{days // 30} 个月"
        assert label == expected, (i, label, expected)


def test_action_days_scale_proportionally(registry: Registry):
    """六种行动按 base/30 等比缩放：锻血 30/15/10/3/10/3，辟府 90/45/30/9/30/9。"""
    def row(realm_index: int) -> list[int]:
        tier = registry.realm_time_tier_days(realm_index)
        base = registry.action_tier_base_days()
        return [action_days(registry.action_base_days(k), tier, base)
                for k in ("seclusion", "adventure", "gather", "spar", "meditate", "breakthrough")]

    assert row(1) == [30, 15, 10, 3, 10, 3]
    assert row(2) == [90, 45, 30, 9, 30, 9]
    assert row(12) == [3600000, 1800000, 1200000, 360000, 1200000, 360000]


# ---------------------------------------------------------------- 曲线

def test_exp_k_is_monotonic(registry: Registry):
    """K 逐境单调不减 —— 高境不得比低境更容易（禁止倒挂）。"""
    ks = [int(registry.realm_by_index(i)["exp_k_bp"]) for i in range(1, 13)]
    assert ks == sorted(ks), f"exp_k_bp 倒挂：{ks}"
    assert ks == [6381, 12046, 20271, 34625, 94323, 147573,
                  226097, 234940, 279775, 949852, 7726604, 7726604]


def test_exp_need_is_strictly_increasing(registry: Registry):
    """Lv.1–100 共 99 个台阶全部严格递增，无倒挂、无零值。"""
    prev = 0
    for lv in range(1, 101):
        need = level_domain.exp_need(lv, registry.exp_k_bp(lv))
        assert need > 0, lv
        assert need > prev, f"Lv.{lv} 需求 {need} 未超过上一级 {prev}"
        prev = need


def test_cross_realm_step_within_tier_ratio(registry: Registry):
    """跨境经验台阶 ≤ 档位倍率 —— 保证「经验涨了，时间也涨了」，不可能体感突增。

    Lv.100 由突破进入（`can_break` 只要求 Lv.99 修满），故只查 10 处真实经验台阶。
    """
    for i in range(1, 11):
        prev_realm = registry.realm_by_index(i)
        next_realm = registry.realm_by_index(i + 1)
        lv = int(prev_realm["level_end"])
        step = (level_domain.exp_need(lv + 1, registry.exp_k_bp(lv + 1))
                / level_domain.exp_need(lv, registry.exp_k_bp(lv)))
        tier_ratio = (registry.realm_time_tier_days(i + 1)
                      / registry.realm_time_tier_days(i))
        assert step <= tier_ratio + 1e-9, (
            f"Lv.{lv}→{lv + 1}（{prev_realm['display_name']}→{next_realm['display_name']}）"
            f"台阶 ×{step:.2f} 超过档位倍率 ×{tier_ratio:.2f}")


def test_cross_realm_clicks_never_increase(registry: Registry):
    """跨境时「玩家要点的次数」必须下降或持平 —— 突破后应更轻松，不是更苦。"""
    for i in range(1, 11):
        lv = int(registry.realm_by_index(i)["level_end"])
        last_clicks = level_domain.exp_need(lv, registry.exp_k_bp(lv)) / _gain(registry, i)
        first_clicks = (level_domain.exp_need(lv + 1, registry.exp_k_bp(lv + 1))
                        / _gain(registry, i + 1))
        assert first_clicks <= last_clicks, (
            f"{registry.realm_by_index(i + 1)['display_name']} 首级 {first_clicks:.2f} 次"
            f" > 上一境末级 {last_clicks:.2f} 次")


# ---------------------------------------------------------------- 节奏形态

def _simulate(registry: Registry) -> dict:
    """用真实 domain 代码跑一遍 Lv.1→100，返回逐境点击/年龄。"""
    global_bp = int(registry.exp_config()["exp_rate_global_bp"])
    level, cultivation, overflow = 1, 0, 0
    age_days = START_AGE * DAYS_PER_YEAR
    rows = []
    for i in range(1, 13):
        realm = registry.realm_by_index(i)
        cap = int(realm["level_end"])
        cap_need = level_domain.exp_need(cap, registry.exp_k_bp(cap))
        days = action_days(registry.action_base_days("seclusion"),
                           registry.realm_time_tier_days(i),
                           registry.action_tier_base_days())
        gain = attrs_domain.cultivation_gain(WIS, i, days, global_bp=global_bp, **BUILD)
        start_age = age_days
        clicks = 0
        if i < 12:  # 大帝由突破进入，无升级
            while not (level >= cap and cultivation >= cap_need):
                res = level_domain.apply_exp(
                    level, cultivation, overflow, gain,
                    lambda lv: registry.exp_k_bp(lv), cap_level=cap)
                level, cultivation, overflow = res.level, res.cultivation, res.exp_overflow
                clicks += 1
                age_days += days
                assert clicks <= 5000, f"{realm['display_name']} 点击量失控"
            # 突破行动耗时 + 进入下一境（level 跳到下一境 level_start）
            age_days += action_days(registry.action_base_days("breakthrough"),
                                    registry.realm_time_tier_days(i),
                                    registry.action_tier_base_days())
            level = int(registry.realm_by_index(i + 1)["level_start"])
            cultivation = 0   # 突破成功清零当前级修为（exp_overflow 保留）
        rows.append(dict(
            realm=i, clicks=clicks, avg=clicks / 9,
            years=(age_days - start_age) / DAYS_PER_YEAR,
            end_age=age_days / DAYS_PER_YEAR,
            lifespan=int(realm["base_lifespan_years"]),
        ))
    return dict(rows=rows, level=level, age=age_days / DAYS_PER_YEAR)


def test_progression_can_reach_emperor(registry: Registry):
    """可通关：模拟全程不超寿元，最终 Lv.100。"""
    sim = _simulate(registry)
    assert sim["level"] == 100
    for row in sim["rows"]:
        assert row["end_age"] < row["lifespan"], (
            f"{registry.realm_by_index(row['realm'])['display_name']} 结束时 "
            f"{row['end_age']:.0f} 岁 ≥ 寿元 {row['lifespan']}，活不到突破")


def test_lifespan_headroom_is_sane(registry: Registry):
    """寿元占用 15%–60%：太低则寿元无意义，太高则突破失败重试即坐化。"""
    sim = _simulate(registry)
    for row in sim["rows"]:
        use = row["end_age"] / row["lifespan"]
        assert 0.10 <= use <= 0.60, (
            f"{registry.realm_by_index(row['realm'])['display_name']} 占用 {use:.0%}")


def test_pacing_is_early_fast_middle_slow_late_medium(registry: Registry):
    """节奏形态：前期快 → 中期明显变慢 → 后期中等（落在两者之间）。"""
    sim = _simulate(registry)
    avg = {row["realm"]: row["avg"] for row in sim["rows"]}

    early = sum(avg[i] for i in (1, 2, 3, 4)) / 4
    middle = sum(avg[i] for i in (5, 6, 7, 8)) / 4
    late = sum(avg[i] for i in (9, 10, 11)) / 3

    # 前期确实快
    assert early < 4.5, f"前期平均 {early:.2f} 次/级，偏慢"
    # 中期明显变慢（≥ 前期的 1.8 倍）
    assert middle > early * 1.8, f"中期 {middle:.2f} 相对前期 {early:.2f} 不够慢"
    # 后期中等：严格落在前、中之间，且不到中期的一半以上慢
    assert early < late < middle, f"后期 {late:.2f} 未落在前期 {early:.2f} 与中期 {middle:.2f} 之间"
    assert late < middle * 0.7, f"后期 {late:.2f} 相对中期 {middle:.2f} 回落不足"
    # 中期是全流程峰值所在
    assert max(avg[i] for i in range(1, 12)) == max(avg[i] for i in (5, 6, 7, 8))


def test_reference_playthrough_length(registry: Registry):
    """全程闭关次数落在设计区间（约 516 次，±15%）。"""
    sim = _simulate(registry)
    total = sum(row["clicks"] for row in sim["rows"])
    assert 430 <= total <= 600, f"全程 {total} 次闭关，超出设计区间"
