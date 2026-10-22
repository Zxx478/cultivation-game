"""修为推进模拟器：用**真实 domain 代码**跑 Lv.1→100，对比旧/新配置。

与 ``gen_pacing_table.py`` 的区别：
- 后者是**解析式**推演（sum(exp)/gain），用于反解 K；
- 本脚本是**逐步模拟**（调用 ``level_domain.apply_exp``），会如实反映
  ``max_consecutive_ups`` 连升上限、floor 取整、溢出结转。

用法：
    python tools/sim_progression.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domain import level as level_domain  # noqa: E402

DAYS_PER_YEAR = 360
START_AGE = 16
WIS = 20
BUILD_COEF = 1.35
GLOBAL_BP = 15000
LEVEL_END = [9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 99, 100]
REALM_NAMES = [
    "锻血", "辟府", "化灵", "刻纹", "布阵", "尊者",
    "燃灯", "归一", "圣人", "大圣", "准帝", "大帝",
]

# ---------------- 旧配置（realms.yaml + game.yaml 现状） ----------------
OLD_K_BP = [10000, 18000, 30000, 45000, 65000, 90000,
            120000, 160000, 220000, 300000, 420000, 420000]
OLD_BASE_DAYS = {"seclusion": 30, "adventure": 15, "gather": 10,
                 "spar": 3, "meditate": 10, "breakthrough": 3}
OLD_FACTOR = 0.40
OLD_LIFESPAN = [100, 150, 200, 300, 400, 600, 800, 1200, 2000, 3000, 5000, 10000]

# ---------------- 新配置（docs/12 §3.1 / §4.2 / §7.2） ----------------
NEW_K_BP = [6381, 12046, 20271, 34625, 94323, 147573,
            226097, 234940, 279775, 949852, 7726604, 7726604]
NEW_TIER_DAYS = [30, 90, 180, 360, 1080, 1800, 2880, 3600, 7200, 36000, 360000, 3600000]
NEW_LIFESPAN = [100, 150, 200, 300, 600, 1200, 2500, 4000, 6000, 13000, 80000, 120000]
TIER_BASE_DAYS = 30


def exp_rate(realm_index: int, global_bp: int = GLOBAL_BP) -> float:
    return ((5.0 + WIS * 0.8) * (1.0 + 0.35 * (realm_index - 1))
            * BUILD_COEF * (global_bp / 10000.0))


def old_action_days(base: int, realm_index: int) -> int:
    raw = base * (1.0 + OLD_FACTOR * (realm_index - 1))
    return max(1, int(math.floor(raw + 0.5)))


def new_action_days(base: int, realm_index: int) -> int:
    return max(1, int(math.floor(base * NEW_TIER_DAYS[realm_index - 1] / TIER_BASE_DAYS + 0.5)))


def simulate(k_bp: list[int], days_of, lifespan: list[int],
             global_bp: int = GLOBAL_BP) -> dict:
    """模拟全程闭关，返回逐境统计。

    口径与引擎一致：境界内要升到 ``level_end`` **并且把该级修满**
    （``state_service.can_break`` 要求 ``cultivation >= exp_need(level_end)``）
    才能突破；突破本身另耗 ``breakthrough`` 行动日数。
    """
    level, cultivation, overflow = 1, 0, 0
    age_days = START_AGE * DAYS_PER_YEAR
    rows = []
    total_clicks = 0
    for idx in range(12):
        r = idx + 1
        gain = int(math.floor(exp_rate(r, global_bp) * days_of(OLD_BASE_DAYS["seclusion"], r)))
        cap = LEVEL_END[idx]
        cap_need = level_domain.exp_need(cap, k_bp[idx])
        realm_start_age = age_days
        clicks = 0
        died = False
        # 大帝（终局）无升级，直接由突破进入
        if r < 12:
            while not (level >= cap and cultivation >= cap_need):
                res = level_domain.apply_exp(
                    level, cultivation, overflow, gain,
                    lambda lv: k_bp[min(11, (lv - 1) // 9)], cap_level=cap)
                level, cultivation, overflow = res.level, res.cultivation, res.exp_overflow
                clicks += 1
                total_clicks += 1
                age_days += days_of(OLD_BASE_DAYS["seclusion"], r)
                if age_days >= lifespan[idx] * DAYS_PER_YEAR:
                    died = True
                    break
                if clicks > 200000:
                    raise RuntimeError("死循环")
        rows.append(dict(
            r=r, name=REALM_NAMES[idx], clicks=clicks,
            avg=clicks / 9, years=(age_days - realm_start_age) / DAYS_PER_YEAR,
            end_age=age_days / DAYS_PER_YEAR, died=died,
            gain=gain, last_need=cap_need,
        ))
        if died:
            break
        if r < 12:
            # 突破行动耗时（按当前境界档位缩放）
            age_days += days_of(OLD_BASE_DAYS["breakthrough"], r)
    return dict(rows=rows, total_clicks=total_clicks,
                total_years=(age_days - START_AGE * DAYS_PER_YEAR) / DAYS_PER_YEAR,
                final_level=level, died=any(x["died"] for x in rows))


def report(title: str, sim: dict, lifespan: list[int]) -> None:
    print(f"\n### {title}\n")
    print("| 序 | 境界 | 本境次数 | 平均/级 | 本境耗时(年) | 结束年龄 | 寿元 | 占用 | 状态 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for row in sim["rows"]:
        life = lifespan[row["r"] - 1]
        use = row["end_age"] / life
        state = "✘ 坐化" if row["died"] else ("◐ 偏紧" if use > 0.60 else "✔")
        print(f"| {row['r']} | {row['name']} | {row['clicks']:,} | {row['avg']:.2f} | "
              f"{row['years']:,.1f} | {row['end_age']:,.0f} | {life:,} | {use:.0%} | {state} |")
    print(f"\n- 总闭关次数：**{sim['total_clicks']:,}**")
    print(f"- 总历时：**{sim['total_years']:,.0f}** 年")
    print(f"- 终局等级：Lv.{sim['final_level']}"
          + ("　**中途坐化**" if sim["died"] else ""))


def main() -> None:
    old = simulate(OLD_K_BP, old_action_days, OLD_LIFESPAN)
    new = simulate(NEW_K_BP, new_action_days, NEW_LIFESPAN)

    print("# 旧 / 新配置逐步模拟对照（真实 domain 代码）\n")
    print("参考修士：悟性 20、综合加成 1.35、全局 1.50；起始年龄 16。")
    report("旧配置（base×(1+0.40×(R−1))，K 10000→420000，寿元 100→10000）",
           old, OLD_LIFESPAN)
    report("新配置（12 档查表，K 6381→7726604，寿元 100→120000）",
           new, NEW_LIFESPAN)

    # ---- MVP 窗口（Lv.1–18）对照 ----
    def mvp(sim: dict) -> tuple[int, float]:
        rows = sim["rows"][:2]
        return sum(x["clicks"] for x in rows), sum(x["years"] for x in rows)

    oc, oy = mvp(old)
    nc, ny = mvp(new)
    print("\n### MVP 窗口（Lv.1–18）对照\n")
    print("| 指标 | 旧 | 新 | 倍数 |")
    print("|---|---|---|---|")
    print(f"| 总闭关次数 | {oc} | {nc} | ×{nc / oc:.2f} |")
    print(f"| 总耗时(年) | {oy:.1f} | {ny:.1f} | ×{ny / oy:.2f} |")
    print(f"| 锻血次数 | {old['rows'][0]['clicks']} | {new['rows'][0]['clicks']} | "
          f"×{new['rows'][0]['clicks'] / old['rows'][0]['clicks']:.2f} |")
    print(f"| 辟府次数 | {old['rows'][1]['clicks']} | {new['rows'][1]['clicks']} | "
          f"×{new['rows'][1]['clicks'] / old['rows'][1]['clicks']:.2f} |")

    # ---- 旧配置逐境单级点击量（问题证据，直接从配置算，不看模拟是否走完） ----
    print("\n### 旧配置逐境单级点击量（问题证据）\n")
    print("| 序 | 境界 | 末级需求 | 单次产出 | 末级需点击 |")
    print("|---|---|---|---|---|")
    for idx in range(12):
        r = idx + 1
        gain = int(math.floor(exp_rate(r) * old_action_days(OLD_BASE_DAYS["seclusion"], r)))
        need = level_domain.exp_need(LEVEL_END[idx], OLD_K_BP[idx])
        print(f"| {r} | {REALM_NAMES[idx]} | {need:,} | {gain:,} | {need / gain:.0f} 次 |")

    # ---- 灵敏度：全局倍率是唯一的整体调速旋钮 ----
    print("\n### 全局倍率灵敏度（`exp_rate_global_bp`）\n")
    print("| global_bp | MVP 次数 | 全程次数 | 终局年龄 | 最大寿元占用 | 能否通关 |")
    print("|---|---|---|---|---|---|")
    for bp in (10000, 12000, 15000, 18000, 24000):
        sim = simulate(NEW_K_BP, new_action_days, NEW_LIFESPAN, global_bp=bp)
        use = max(row["end_age"] / NEW_LIFESPAN[row["r"] - 1] for row in sim["rows"])
        clicks, _ = mvp(sim)
        ok = "✔" if (not sim["died"] and use <= 0.72) else "✘"
        print(f"| {bp:,} | {clicks} | {sim['total_clicks']:,} | {sim['total_years']:,.0f} | "
              f"{use:.0%} | {ok} |")


if __name__ == "__main__":
    main()
