"""修为曲线与升级结算。

公式（PRD §3.2 / 任务书 §8 红线 2）::

    Exp(L) = floor(100 × L^1.78 × K(realm))

- ``K`` 为该等级所属大境的 ``exp_k_bp / 10000``（锻血 1.0、辟府 1.8 …）；
- 关键节点自检：Lv.1→2 = 100，Lv.9→10 = 4,995，Lv.18→19 = 30,878；
- 单次结算最多连升 **5** 级，超出部分进 ``exp_overflow`` 不丢失；
- ``cap_level``（MVP = 18）封顶：到顶后修为只累积、不再升级。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Union

STAGES_PER_REALM: int = 9
DEFAULT_CAP_LEVEL: int = 18
DEFAULT_MAX_UPS: int = 5

LevelKSource = Union[int, Callable[[int], int]]


@dataclass(frozen=True)
class ApplyExpResult:
    """一次修为结算的结果。

    Attributes:
        level: 结算后等级。
        cultivation: 结算后当前等级内累计修为（可能 = 需求值，表示卡在连升上限）。
        exp_overflow: 结算后溢出池。
        levels_gained: 本次连升级数（0–5）。
        overflow_added: 本次新进溢出池的修为。
        capped: 是否已达等级上限（未再升级）。
    """

    level: int
    cultivation: int
    exp_overflow: int
    levels_gained: int
    overflow_added: int
    capped: bool


def _k(value: LevelKSource, level: int) -> int:
    """把「常数 K」与「按等级的 K 函数」统一成整数 bp。"""
    return int(value(level)) if callable(value) else int(value)


def exp_need(level: int, exp_k_bp: int = 10000) -> int:
    """升到下一级所需修为。

    Args:
        level: 当前全局等级（1–100）。
        exp_k_bp: 所属境界的曲线系数（bp，10000 = 1.0）。

    Returns:
        所需修为（整数，向下取整）。

    Raises:
        ValueError: 参数越界。
    """
    if level < 1:
        raise ValueError("level must be >= 1")
    if exp_k_bp <= 0:
        raise ValueError("exp_k_bp must be > 0")
    return int(math.floor(100.0 * (level ** 1.78) * (exp_k_bp / 10000.0)))


def realm_index_of(level: int) -> int:
    """全局等级 → 大境序号（每境 9 级，Lv.100 为第 12 境）。"""
    if level < 1:
        raise ValueError("level must be >= 1")
    return (level - 1) // STAGES_PER_REALM + 1


def realm_stage_of(level: int) -> int:
    """全局等级 → 小境序号（1–9）。"""
    if level < 1:
        raise ValueError("level must be >= 1")
    return (level - 1) % STAGES_PER_REALM + 1


def apply_exp(level: int, cultivation: int, exp_overflow: int, gain: int,
              exp_k_bp: LevelKSource, *, cap_level: int = DEFAULT_CAP_LEVEL,
              max_ups: int = DEFAULT_MAX_UPS) -> ApplyExpResult:
    """结算一次修为收益（连升 + 溢出保留 + 封顶）。

    Args:
        level: 当前等级。
        cultivation: 当前等级内累计修为。
        exp_overflow: 当前溢出池。
        gain: 本次获得的修为（可为 0，不可为负）。
        exp_k_bp: 常数或 ``k(level)`` 函数（跨大境时 K 会变）。
        cap_level: 等级上限（MVP 18）。
        max_ups: 单次最多连升级数（5）。

    Returns:
        ``ApplyExpResult``。

    Raises:
        ValueError: 参数非法。
    """
    if gain < 0:
        raise ValueError("gain must be >= 0")
    if level < 1 or level > cap_level:
        raise ValueError(f"level {level} out of range 1..{cap_level}")
    if cultivation < 0 or exp_overflow < 0:
        raise ValueError("cultivation / exp_overflow must be >= 0")

    current = level
    pool = cultivation + gain
    ups = 0
    overflow_added = 0
    capped = False

    while current < cap_level and ups < max_ups:
        need = exp_need(current, _k(exp_k_bp, current))
        if pool < need:
            break
        pool -= need
        current += 1
        ups += 1

    if current >= cap_level:
        # 封顶：修为照常累积，但不再升级
        capped = True
    elif ups >= max_ups:
        # 卡在连升上限：把「够下一级的部分之外的余量」记进溢出池，
        # 保证 结算前后 (cultivation + overflow) 守恒，且下次结算可立刻续升。
        need = exp_need(current, _k(exp_k_bp, current))
        if pool > need:
            overflow_added = pool - need
            pool = need

    return ApplyExpResult(
        level=current,
        cultivation=pool,
        exp_overflow=exp_overflow + overflow_added,
        levels_gained=ups,
        overflow_added=overflow_added,
        capped=capped,
    )


def is_level_full(level: int, cultivation: int, exp_k_bp: int) -> bool:
    """当前等级修为是否已满（突破前置之一）。"""
    return cultivation >= exp_need(level, exp_k_bp)


def progress_bp(level: int, cultivation: int, exp_k_bp: int) -> int:
    """当前等级进度（bp，0–10000），用于修为条。"""
    need = exp_need(level, exp_k_bp)
    if need <= 0:
        return 0
    return max(0, min(10000, int(cultivation * 10000 // need)))


def exp_for_levels_to(level: int, target: int, k_of_level: Callable[[int], int]) -> int:
    """从 ``level`` 升到 ``target`` 需要的总修为（含起始等级，不含 target 级本身）。"""
    if target <= level:
        return 0
    return sum(exp_need(lv, k_of_level(lv)) for lv in range(level, target))
