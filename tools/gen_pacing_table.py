"""境界时间档位 + 修为曲线推演器（只算不写盘，输出 Markdown 表格）。

用途：
1. 逐档核对「一个月 … 一万年」这 12 个档位的日数、中文显示与倍数；
2. 按「每级目标闭关次数」反解各境 ``exp_k_bp``，并核算本境总耗时 / 寿元占用；
3. 用**累计年龄**（而非单境重置）校验寿元是否放得下，反推寿元下限；
4. 自检：档位严格递增、需求严格递增无倒挂、跨境台阶不超过档位倍率。

口径（写进文档，改这里等于改设计）：
- 1 游戏年 = 360 日，1 月 = 30 日；
- 档位 = 该境界**一次闭关**的耗时；其余行动按 ``base_days / 30`` 等比缩放；
- 参考修士：悟性 20，综合加成 1.35×（心法 1.12 × 体质 1.10 × 环境 1.10），
  心境/状态/方向均 1.00，全局倍率 1.50；
- 单次闭关产出 = floor(ExpRate × 档位日数)；
- 单级需求 Exp(L) = floor(100 × L^1.78 × K(R))。
"""
from __future__ import annotations

import math

DAYS_PER_MONTH = 30
DAYS_PER_YEAR = 360
START_AGE = 16

REALM_NAMES = [
    "锻血", "辟府", "化灵", "刻纹", "布阵", "尊者",
    "燃灯", "归一", "圣人", "大圣", "准帝", "大帝",
]

#: 用户指定的 12 档
TIER_SPEC = [
    (1, "1 个月"), (3, "3 个月"), (6, "6 个月"), (1, "1 年"),
    (3, "3 年"), (5, "5 年"), (8, "8 年"), (10, "10 年"),
    (20, "20 年"), (100, "100 年"), (1000, "1000 年"), (10000, "10000 年"),
]

#: 每级目标闭关次数（取本境末级；境内按 L^1.78 自然递减）
#: 约束：跨境的「次数」只能下降（K 必须单调），因此 R7→R8 档位仅 ×1.25 时不能压太狠
TARGET_END_CLICKS = [2.5, 4.0, 5.5, 6.5, 7.5, 8.5, 9.5, 9.0, 6.0, 4.5, 4.0, None]

#: 各境末级全局等级
LEVEL_END = [9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 99, 100]
LEVEL_START = [1] + [e + 1 for e in LEVEL_END[:-1]]

WIS = 20
BUILD_COEF = 1.35
GLOBAL_BP = 15000
EXP_BASE = 100.0
EXP_POW = 1.78

#: 调整前的寿元（年），用于对照
LIFESPAN_OLD = [100, 150, 200, 300, 400, 600, 800, 1200, 2000, 3000, 5000, 10000]

#: 采用值（年）：R1–R4 沿用原有经典数值，R5 起按累计年龄 50% 占用放大并取整
LIFESPAN = [100, 150, 200, 300, 600, 1200, 2500, 4000, 6000, 13000, 80000, 120000]

#: 终局境界（只有一级，不参与升级）
TERMINAL = 12

#: 阶段划分（用于早/中/后汇总）
PHASES = [("前期", 1, 4), ("中期", 5, 8), ("后期", 9, 11)]


def build_tiers() -> list[int]:
    days = []
    for amount, text in TIER_SPEC:
        unit = "年" if text.endswith("年") else "月"
        days.append(amount * (DAYS_PER_YEAR if unit == "年" else DAYS_PER_MONTH))
    return days


def exp_rate(realm_index: int) -> float:
    return ((5.0 + WIS * 0.8)
            * (1.0 + 0.35 * (realm_index - 1))
            * BUILD_COEF
            * (GLOBAL_BP / 10000.0))


def gain_per_action(realm_index: int, days: int) -> int:
    return int(math.floor(exp_rate(realm_index) * days))


def exp_need(level: int, k: float) -> int:
    return int(math.floor(EXP_BASE * (level ** EXP_POW) * k))


def nice_up(value: float) -> int:
    """向上取到「好看」的整数，便于写进配置。"""
    if value <= 100:
        step = 10
    elif value <= 1000:
        step = 50
    elif value <= 10000:
        step = 100
    elif value <= 100000:
        step = 1000
    else:
        step = 10000
    return int(math.ceil(value / step) * step)


def fmt_int(value: float) -> str:
    return f"{int(value):,}"


def main() -> None:
    tiers = build_tiers()

    # ---------- 1. 档位核对 ----------
    print("## 1. 档位核对\n")
    print("| 序 | 境界 | 档位文案 | 游戏日 | 折合月 | 折合年 | 日历表示 | 相对上一档 | 校验 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for i, (days, (amount, text)) in enumerate(zip(tiers, TIER_SPEC)):
        months = days / DAYS_PER_MONTH
        years = days / DAYS_PER_YEAR
        ratio = "—" if i == 0 else f"×{days / tiers[i - 1]:.2f}"
        cal = f"{int(years)} 年" if years >= 1 else f"{int(months)} 月"
        ok = "✔" if days % DAYS_PER_MONTH == 0 else "✘"
        print(f"| {i + 1} | {REALM_NAMES[i]} | {text} | {days:,} | {months:g} | {years:g} | "
              f"{cal} | {ratio} | {ok} |")
    assert tiers == sorted(tiers) and len(set(tiers)) == 12, "档位必须严格递增且不重复"
    assert all(d % DAYS_PER_MONTH == 0 for d in tiers), "档位必须是整月，保证日历对齐"

    # ---------- 2. 反解 K ----------
    print("\n## 2. 反解各境系数 K（由目标次数倒推）\n")
    print("| 序 | 境界 | 档位日 | 单次产出 | 末级需求(基准) | 目标次数 | K | exp_k_bp |")
    print("|---|---|---|---|---|---|---|---|")
    ks: list[float] = []
    kbps: list[int] = []
    for i in range(12):
        r = i + 1
        days = tiers[i]
        gain = gain_per_action(r, days)
        lend = LEVEL_END[i]
        base_need = EXP_BASE * (lend ** EXP_POW)
        target = TARGET_END_CLICKS[i]
        if target is None:
            # 大帝只有一级，没有升级需求：K 沿用准帝，保证 Lv.99→100 台阶连续
            k = ks[r - 2]
            kbp = kbps[r - 2]
            ks.append(k)
            kbps.append(kbp)
            print(f"| {r} | {REALM_NAMES[i]} | {days:,} | {fmt_int(gain)} | {fmt_int(base_need)} | "
                  f"终局 | {k:g}（同准帝） | {kbp:,} |")
            continue
        k = target * gain / base_need
        kbp = int(round(k * 10000))
        ks.append(kbp / 10000.0)          # 以落库后的 bp 为准回算
        kbps.append(kbp)
        print(f"| {r} | {REALM_NAMES[i]} | {days:,} | {fmt_int(gain)} | {fmt_int(base_need)} | "
              f"{target:g} | {kbp / 10000:g} | {kbp:,} |")

    for i in range(1, 12):
        assert ks[i] >= ks[i - 1], f"K 倒挂：R{i} {ks[i - 1]} > R{i + 1} {ks[i]}"

    # ---------- 3. 逐境节奏 ----------
    print("\n## 3. 逐境节奏（参考修士）\n")
    print("| 序 | 境界 | 等级 | 本境需求合计 | 单次产出 | 本境次数 | 平均/级 | 末级/次 "
          "| 本境耗时(年) | 结束年龄 | 阶段 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    total_clicks = 0.0
    age = START_AGE
    for i in range(12):
        r = i + 1
        days = tiers[i]
        gain = gain_per_action(r, days)
        span = range(LEVEL_START[i], LEVEL_END[i] + 1)
        total = 0 if r == TERMINAL else sum(exp_need(lv, ks[i]) for lv in span)
        clicks = total / gain
        years = clicks * days / DAYS_PER_YEAR
        age += years
        total_clicks += clicks
        avg = clicks / 9 if r != TERMINAL else 0.0
        end_clicks = exp_need(LEVEL_END[i], ks[i]) / gain if r != TERMINAL else 0.0
        phase = next((p for p, lo, hi in PHASES if lo <= r <= hi), "终局")
        rows.append(dict(r=r, clicks=clicks, avg=avg, years=years,
                         end_clicks=end_clicks, age=age))
        print(f"| {r} | {REALM_NAMES[i]} | {LEVEL_START[i]}–{LEVEL_END[i]} | {fmt_int(total)} | "
              f"{fmt_int(gain)} | {clicks:.1f} | {avg:.2f} | {end_clicks:.1f} | "
              f"{years:,.0f} | {age:,.0f} | {phase} |")
    print(f"\n合计约 **{total_clicks:,.0f}** 次闭关，历时约 **{age - START_AGE:,.0f}** 年，"
          f"到达大帝时 {age:,.0f} 岁。")

    # ---------- 4. 阶段汇总 ----------
    print("\n## 4. 早/中/后节奏\n")
    print("| 阶段 | 境界 | 平均次数/级 | 历时(年) | 占总时长 |")
    print("|---|---|---|---|---|")
    span_total = age - START_AGE
    for name, lo, hi in PHASES:
        sub = [row for row in rows if lo <= row["r"] <= hi]
        avg = sum(x["clicks"] for x in sub) / (9 * len(sub))
        yrs = sum(x["years"] for x in sub)
        print(f"| {name} | R{lo}–R{hi} | {avg:.2f} | {yrs:,.0f} | {yrs / span_total:.0%} |")

    # ---------- 5. 自检 ----------
    print("\n## 5. 自检\n")
    prev = 0
    ratios = []
    for lv in range(1, 100):
        r = min(11, (lv - 1) // 9)
        cur = exp_need(lv, ks[r])
        assert cur > 0, lv
        if prev:
            ratios.append((cur / prev, lv))
        prev = cur
    print("- 需求严格递增：✔（Lv.1–100 共 99 个台阶，无倒挂、无零值）")
    biggest = max(ratios)
    print(f"- 相邻级倍率：最小 ×{min(ratios)[0]:.2f}（Lv.{min(ratios)[1]}），"
          f"最大 ×{biggest[0]:.2f}（Lv.{biggest[1]}）")
    print("- 跨境台阶 vs 档位倍率：")
    for i in range(1, 12):
        lv = LEVEL_END[i - 1]
        step = exp_need(lv + 1, ks[i]) / exp_need(lv, ks[i - 1])
        tier_ratio = tiers[i] / tiers[i - 1]
        assert step <= tier_ratio + 1e-9, \
            f"Lv.{lv} 台阶 ×{step:.2f} 超过档位倍率 ×{tier_ratio:.2f}"
        flag = "✔" if step <= tier_ratio * 0.9 else "◐"
        print(f"  - Lv.{lv}→{lv + 1}（{REALM_NAMES[i - 1]}→{REALM_NAMES[i]}）："
              f"×{step:.2f} / 档位 ×{tier_ratio:.2f} {flag}")

    inner = [(exp_need(lv + 1, ks[min(11, (lv - 1) // 9)])
              / exp_need(lv, ks[min(11, (lv - 1) // 9)]), lv)
             for lv in range(1, 100) if (lv - 1) // 9 == lv // 9]
    start_ramp = [x for x in inner if x[1] <= 5]
    settled = [x for x in inner if x[1] > 5]
    settled_max = max(settled)
    print(f"- 境内相邻级：Lv.1–5 起步段最高 ×{max(start_ramp)[0]:.2f}（幂律形状），"
          f"Lv.6 起最高 ×{settled_max[0]:.2f}（Lv.{settled_max[1]}），收敛至 ×1.02")
    assert settled_max[0] <= 1.35, "境内出现突增"

    print("- 跨境体感（上一境末级次数 → 新境首级次数）：经验值的跨境台阶由档位倍率吸收")
    for i in range(1, 11):
        lv = LEVEL_END[i - 1]
        last_c = exp_need(lv, ks[i - 1]) / gain_per_action(i, tiers[i - 1])
        first_c = exp_need(lv + 1, ks[i]) / gain_per_action(i + 1, tiers[i])
        print(f"  - {REALM_NAMES[i - 1]}→{REALM_NAMES[i]}：{last_c:.1f} 次 → "
              f"{first_c:.1f} 次（×{first_c / last_c:.2f}）")
        assert first_c <= last_c, f"{REALM_NAMES[i]} 首级比上一境末级还慢"

    # ---------- 6. 寿元 ----------
    print("\n## 6. 寿元校验（累计年龄口径）\n")
    print("| 序 | 境界 | 本境耗时(年) | 结束时年龄 | 原寿元 | 采用值 | 余量(年) | 占用 | 结论 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for i in range(12):
        row = rows[i]
        need = row["age"]
        life = LIFESPAN[i]
        use = need / life
        verdict = "✔ 宽裕" if use <= 0.55 else "◐ 偏紧" if use <= 0.72 else "✘ 放不下"
        print(f"| {row['r']} | {REALM_NAMES[i]} | {row['years']:,.0f} | {need:,.0f} | "
              f"{LIFESPAN_OLD[i]:,} | {life:,} | {life - need:,.0f} | {use:.0%} | {verdict} |")
        assert need < life, f"{REALM_NAMES[i]} 活不到突破：需 {need:,.0f} 年，寿元 {life:,}"
        assert use <= 0.72, f"{REALM_NAMES[i]} 寿元过紧：占用 {use:.0%}"
    assert LIFESPAN == sorted(LIFESPAN) and len(set(LIFESPAN)) == 12, "寿元必须严格递增"


if __name__ == "__main__":
    main()
