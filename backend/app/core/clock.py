"""唯一时间入口：游戏日 ↔ 年/月、行动耗时按境界档位缩放。

口径（系统设计 D5 / ``docs/12-境界时间档位与修为曲线设计-v1.0.md``）：
- 1 游戏年 = 360 日 = 12 月 × 30 日；
- ``age_days`` 初值 1，``game_year(d) = (d - 1) // 360 + 1``；
- 每个大境有一档 ``time_tier_days``（锻血 30 日 → 大帝 3,600,000 日），
  行动耗时按 ``base_days × tier_days / ACTION_TIER_BASE_DAYS`` 等比缩放，
  四舍五入到整数日，至少 1 日；
- 锻血（tier=30）闭关 30 日，辟府（tier=90）闭关 90 日。

本模块只做纯算术，不查内容、不认识「境界」——档位由调用方经 Registry 取到后传入。
"""
from __future__ import annotations

import math

DAYS_PER_MONTH: int = 30
MONTHS_PER_YEAR: int = 12
DAYS_PER_YEAR: int = DAYS_PER_MONTH * MONTHS_PER_YEAR
#: 档位缩放基准：锻血档位的日数（= ``game.yaml.action_tier_base_days``）。
ACTION_TIER_BASE_DAYS: int = 30
AGE_DAYS_INITIAL: int = 1


def game_year(age_days: int) -> int:
    """由累计游戏日推算游戏年（第 1 年起算，非 0）。"""
    if age_days < AGE_DAYS_INITIAL:
        raise ValueError("age_days must be >= 1")
    return (age_days - 1) // DAYS_PER_YEAR + 1


def game_month(age_days: int) -> int:
    """由累计游戏日推算**全局**月序号（从 1 起）。"""
    if age_days < AGE_DAYS_INITIAL:
        raise ValueError("age_days must be >= 1")
    return (age_days - 1) // DAYS_PER_MONTH + 1


def month_of_year(age_days: int) -> int:
    """返回当年内的月份（1–12）。"""
    return ((age_days - 1) % DAYS_PER_YEAR) // DAYS_PER_MONTH + 1


def action_days(base_days: int, tier_days: int,
                base_tier_days: int = ACTION_TIER_BASE_DAYS) -> int:
    """行动耗时（按境界档位等比缩放）。

    Args:
        base_days: 基准耗时（``game.yaml.action_base_days`` 的值）。
        tier_days: 当前大境的时间档位日数（``realms.yaml.time_tier_days``）。
        base_tier_days: 档位缩放基准，默认 30（锻血档位）。

    Returns:
        实际耗时游戏日，四舍五入且下限 1。

    Raises:
        ValueError: 参数越界。
    """
    if base_days <= 0:
        raise ValueError("base_days must be > 0")
    if tier_days <= 0:
        raise ValueError("tier_days must be > 0")
    if base_tier_days <= 0:
        raise ValueError("base_tier_days must be > 0")
    raw = base_days * tier_days / base_tier_days
    return max(1, int(math.floor(raw + 0.5)))


def days_to_years(days: int) -> float:
    """游戏日 → 年（浮点，仅用于展示）。"""
    return days / float(DAYS_PER_YEAR)


def years_to_days(years: float) -> int:
    """年 → 游戏日（向下去整）。"""
    return int(math.floor(years * DAYS_PER_YEAR + 1e-9))


def describe_days(days: int) -> str:
    """把游戏日写成「X 年 Y 月 Z 日」，用于日志/UI 文案。"""
    if days < 0:
        raise ValueError("days must be >= 0")
    years, rest = divmod(days, DAYS_PER_YEAR)
    months, dd = divmod(rest, DAYS_PER_MONTH)
    parts = []
    if years:
        parts.append(f"{years} 年")
    if months:
        parts.append(f"{months} 月")
    if dd or not parts:
        parts.append(f"{dd} 日")
    return "".join(parts)
