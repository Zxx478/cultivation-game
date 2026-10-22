"""唯一时间入口：游戏日 ↔ 年/月、行动动态耗时。

口径（任务书 §8 红线 4 / 系统设计 D5）：
- 1 游戏年 = 360 日 = 12 月 × 30 日；
- ``age_days`` 初值 1，``game_year(d) = (d - 1) // 360 + 1``；
- 行动耗时 ``base_days × (1 + realm_time_factor × (realm_index - 1))``，
  四舍五入到整数日，至少 1 日；
- 锻血（realm_index=1）闭关 30 日，辟府（=2）闭关 42 日。
"""
from __future__ import annotations

import math

DAYS_PER_MONTH: int = 30
MONTHS_PER_YEAR: int = 12
DAYS_PER_YEAR: int = DAYS_PER_MONTH * MONTHS_PER_YEAR
REALM_TIME_FACTOR: float = 0.40
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


def action_days(base_days: int, realm_index: int,
                factor: float = REALM_TIME_FACTOR) -> int:
    """行动耗时（含境界时间膨胀）。

    Args:
        base_days: 基准耗时（game.yaml.action_base_days 的值）。
        realm_index: 当前大境序号（1–12）。
        factor: 境界时间膨胀系数，默认 0.40。

    Returns:
        实际耗时游戏日，四舍五入且下限 1。

    Raises:
        ValueError: 参数越界。
    """
    if base_days <= 0:
        raise ValueError("base_days must be > 0")
    if realm_index < 1:
        raise ValueError("realm_index must be >= 1")
    raw = base_days * (1.0 + factor * (realm_index - 1))
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
