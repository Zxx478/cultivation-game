"""突破：成功率明细、准备上限、连败补偿、保底、惩罚、根基、寿元。

口径（任务书 §8 红线 5–6 / 系统设计 §4.3）：
- 成功率 = ``clamp(基础 + 体质 + 根基 + 准备 + 连败补偿, 5%, 95%)``；
- 冲关准备（丹药/护法/吉日/灵地）四类合计 **≤ +30%**；
- ``fail_streak ≥ 5`` → 下一次 ``final_bp = 10000`` 且 ``guaranteed = True``；
- 失败按 ``final_bp`` 分三档惩罚：≥60% 轻伤 / 30–60% 中伤 / <30% 重伤；
- 根基按「本境突破尝试次数」判定：1 圆满 / 2 扎实 / 3 虚浮 / ≥4 崩坏；
- 寿元 < 3600 日（10 年）禁止突破；失败后冷却 30 日。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..core.clock import DAYS_PER_YEAR
from ..core.errors import (
    BREAKTHROUGH_COOLDOWN,
    BREAKTHROUGH_LIFESPAN_LOW,
    CULTIVATION_INSUFFICIENT,
    GameError,
)
from .level import exp_need

DEFAULT_CFG: Dict[str, Any] = {
    "chance_min_bp": 500,
    "chance_max_bp": 9500,
    "prepare_cap_bp": 3000,
    "pill_bonus_bp": 800,
    "pill_max_count": 2,
    "guardian_bonus_bp": 1000,
    "omen_bonus_bp": 600,
    "land_bonus_bp": 600,
    "fail_bonus_bp": 1500,
    "fail_bonus_cap_bp": 3000,
    "guaranteed_after_fails": 5,
    "cooldown_days": 30,
    "min_lifespan_days": 3600,
    "hp_required_bp": 8000,
    "penalty_tiers": (
        {"min_bp": 6000, "name": "light", "cultivation_loss_bp": 1000,
         "hp_loss_bp": 3000, "dao_heart": 0, "qi_deviation_bp": 0},
        {"min_bp": 3000, "name": "medium", "cultivation_loss_bp": 2000,
         "hp_loss_bp": 6000, "dao_heart": 0, "qi_deviation_bp": 0},
        {"min_bp": 0, "name": "heavy", "cultivation_loss_bp": 3500,
         "hp_loss_bp": 9000, "dao_heart": 5, "qi_deviation_bp": 500},
    ),
    "foundation": (
        {"attempts": 1, "name": "圆满", "attr_bonus_bp": 500, "next_realm_bp": 300},
        {"attempts": 2, "name": "扎实", "attr_bonus_bp": 200, "next_realm_bp": 0},
        {"attempts": 3, "name": "虚浮", "attr_bonus_bp": -300, "next_realm_bp": 0},
        {"attempts": 4, "name": "崩坏", "attr_bonus_bp": -800, "next_realm_bp": -500},
    ),
}


def _cfg(overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """合并外部配置（game.yaml.breakthrough）与缺省。"""
    out = dict(DEFAULT_CFG)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


@dataclass(frozen=True)
class BreakthroughItem:
    """成功率明细的一项（UI 逐项展示 Δ）。"""

    key: str
    label: str
    value_bp: int


@dataclass(frozen=True)
class BreakthroughPreview:
    """突破预览结果。"""

    final_bp: int
    base_bp: int
    items: Tuple[BreakthroughItem, ...]
    guaranteed: bool
    prepare_bp: int
    prepare_capped: bool
    raw_bp: int
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """转字典（含明细数组与附加信息）。"""
        return {
            "final_bp": self.final_bp,
            "base_bp": self.base_bp,
            "items": [asdict(i) for i in self.items],
            "guaranteed": self.guaranteed,
            "prepare_bp": self.prepare_bp,
            "prepare_capped": self.prepare_capped,
            "raw_bp": self.raw_bp,
            **dict(self.extras),
        }


def preview_breakthrough(
    base_bp: int,
    *,
    physique_bp: int = 0,
    foundation_bp: int = 0,
    pill_count: int = 0,
    guardian: bool = False,
    omen: bool = False,
    land: bool = False,
    fail_streak: int = 0,
    cfg: Optional[Mapping[str, Any]] = None,
) -> BreakthroughPreview:
    """计算突破成功率明细。

    Args:
        base_bp: 本境基础成功率（realms.yaml.breakthrough_base_bp）。
        physique_bp: 体质突破加成。
        foundation_bp: 根基加成（见 ``foundation_of``）。
        pill_count: 破境丹数量（≥2 后不再增加）。
        guardian/omen/land: 护法 / 吉日 / 灵地。
        fail_streak: 当前连败次数。
        cfg: ``game.yaml.breakthrough``（None 用缺省）。

    Returns:
        ``BreakthroughPreview``。
    """
    conf = _cfg(cfg)
    pills = max(0, min(int(pill_count), int(conf["pill_max_count"])))
    pill_bp = pills * int(conf["pill_bonus_bp"])
    guardian_bp = int(conf["guardian_bonus_bp"]) if guardian else 0
    omen_bp = int(conf["omen_bonus_bp"]) if omen else 0
    land_bp = int(conf["land_bonus_bp"]) if land else 0

    prepare_raw = pill_bp + guardian_bp + omen_bp + land_bp
    prepare = min(prepare_raw, int(conf["prepare_cap_bp"]))
    prepare_capped = prepare_raw > prepare

    fail_bonus = min(int(fail_streak) * int(conf["fail_bonus_bp"]),
                     int(conf["fail_bonus_cap_bp"]))
    fail_bonus = max(0, fail_bonus)

    items: List[BreakthroughItem] = [
        BreakthroughItem("base", "本境基础", int(base_bp)),
        BreakthroughItem("physique", "体质", int(physique_bp)),
        BreakthroughItem("foundation", "根基", int(foundation_bp)),
        BreakthroughItem("pill", "丹药", pill_bp),
        BreakthroughItem("guardian", "护法", guardian_bp),
        BreakthroughItem("omen", "吉日", omen_bp),
        BreakthroughItem("land", "灵地", land_bp),
        BreakthroughItem("fail_bonus", "连败补偿", fail_bonus),
    ]

    raw = (int(base_bp) + int(physique_bp) + int(foundation_bp)
           + prepare + fail_bonus)
    guaranteed = int(fail_streak) >= int(conf["guaranteed_after_fails"])
    if guaranteed:
        return BreakthroughPreview(
            final_bp=10000,
            base_bp=int(base_bp),
            items=tuple(items),
            guaranteed=True,
            prepare_bp=prepare,
            prepare_capped=prepare_capped,
            raw_bp=raw,
            extras={"guaranteed_reason": "5 连败保底"},
        )

    lo, hi = int(conf["chance_min_bp"]), int(conf["chance_max_bp"])
    final = max(lo, min(hi, raw))
    return BreakthroughPreview(
        final_bp=final,
        base_bp=int(base_bp),
        items=tuple(items),
        guaranteed=False,
        prepare_bp=prepare,
        prepare_capped=prepare_capped,
        raw_bp=raw,
        extras={"clamped": final != raw},
    )


def penalty_of(final_bp: int, cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """按失败时的成功率取惩罚档（三档）。

    Args:
        final_bp: 本次突破的成功率（失败后用于分档）。
        cfg: 配置覆盖。

    Returns:
        ``{name, cultivation_loss_bp, hp_loss_bp, dao_heart, qi_deviation_bp}``。
    """
    conf = _cfg(cfg)
    for tier in conf["penalty_tiers"]:
        if int(final_bp) >= int(tier["min_bp"]):
            return {
                "name": str(tier["name"]),
                "cultivation_loss_bp": int(tier["cultivation_loss_bp"]),
                "hp_loss_bp": int(tier["hp_loss_bp"]),
                "dao_heart": int(tier["dao_heart"]),
                "qi_deviation_bp": int(tier["qi_deviation_bp"]),
            }
    tiers = conf["penalty_tiers"]
    last = tiers[-1]
    return {
        "name": str(last["name"]),
        "cultivation_loss_bp": int(last["cultivation_loss_bp"]),
        "hp_loss_bp": int(last["hp_loss_bp"]),
        "dao_heart": int(last["dao_heart"]),
        "qi_deviation_bp": int(last["qi_deviation_bp"]),
    }


def foundation_of(attempts: int, cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """按本境突破尝试次数判定根基。

    Args:
        attempts: ``character.realm_attempts``（含保底成功那次）。
        cfg: 配置覆盖。

    Returns:
        ``{name, attr_bonus_bp, next_realm_bp, attempts}``（≥4 恒为崩坏）。
    """
    conf = _cfg(cfg)
    table = conf["foundation"]
    taken = max(1, int(attempts))
    chosen = None
    for row in table:
        if taken >= int(row["attempts"]):
            chosen = row
    if chosen is None:  # pragma: no cover - 表为空时兜底
        chosen = table[0]
    return {
        "name": str(chosen["name"]),
        "attr_bonus_bp": int(chosen["attr_bonus_bp"]),
        "next_realm_bp": int(chosen["next_realm_bp"]),
        "attempts": taken,
    }


def foundation_bp(attempts: int, cfg: Optional[Mapping[str, Any]] = None) -> int:
    """只取根基对突破成功率的影响（bp）。"""
    return int(foundation_of(attempts, cfg)["attr_bonus_bp"])


def foundation_next_bp(foundation_name: Any,
                       cfg: Optional[Mapping[str, Any]] = None) -> int:
    """按已记录的根基名，取「对下一次突破」的成功率加成（bp）。

    根基在突破成功时写入 ``character.foundation``，其 ``next_realm_bp``
    应作用于下一境的冲关成功率；未突破过（空串）返回 0。
    """
    conf = _cfg(cfg)
    name = str(foundation_name or "")
    for row in conf["foundation"]:
        if str(row["name"]) == name:
            return int(row["next_realm_bp"])
    return 0


def lifespan_max_days(base_lifespan_years: int, *, extra_years: float = 0.0) -> int:
    """寿元上限（日）：``(基准年 + 加成) × 360``，突破成功时整体重算。"""
    return int((base_lifespan_years + extra_years) * DAYS_PER_YEAR)


def lifespan_left_days(age_days: int, lifespan_max_days_value: int) -> int:
    """剩余寿元（日，可为负）。"""
    return int(lifespan_max_days_value) - int(age_days)


def check_prereq(
    *,
    level: int,
    realm_level_end: int,
    cultivation: int,
    exp_k_bp: int,
    age_days: int,
    lifespan_max_days_value: int,
    last_fail_age_days: Optional[int] = None,
    cfg: Optional[Mapping[str, Any]] = None,
) -> None:
    """校验突破前置（不满足抛 ``GameError``）。

    Raises:
        GameError: 4007 修为不足 / 4008 寿元不足十年 / 4009 冷却未过。
    """
    conf = _cfg(cfg)
    if int(level) < int(realm_level_end):
        raise GameError(CULTIVATION_INSUFFICIENT,
                        f"未达本境顶（需 Lv.{realm_level_end}）",
                        {"level": level, "required_level": realm_level_end})
    need = exp_need(int(level), int(exp_k_bp))
    if int(cultivation) < need:
        raise GameError(CULTIVATION_INSUFFICIENT,
                        "修为未满",
                        {"cultivation": cultivation, "required": need})

    left = lifespan_left_days(age_days, lifespan_max_days_value)
    if left < int(conf["min_lifespan_days"]):
        raise GameError(BREAKTHROUGH_LIFESPAN_LOW,
                        "寿元不足十年，不可冲关",
                        {"lifespan_left_days": left})

    if last_fail_age_days is not None:
        elapsed = int(age_days) - int(last_fail_age_days)
        if elapsed < int(conf["cooldown_days"]):
            raise GameError(BREAKTHROUGH_COOLDOWN,
                            "冲关冷却未过",
                            {"elapsed_days": elapsed,
                             "cooldown_days": int(conf["cooldown_days"])})


def punish_cultivation(cultivation: int, final_bp: int,
                       cfg: Optional[Mapping[str, Any]] = None) -> int:
    """失败后扣减的修为（不低于 0）。"""
    tier = penalty_of(final_bp, cfg)
    loss = int(cultivation * tier["cultivation_loss_bp"] / 10000)
    return max(0, int(cultivation) - loss)


def punish_hp(hp_max: int, final_bp: int,
              cfg: Optional[Mapping[str, Any]] = None) -> int:
    """失败后剩余气血（按气血上限扣比例，至少保留 1 点）。"""
    tier = penalty_of(final_bp, cfg)
    remaining = int(hp_max * (10000 - tier["hp_loss_bp"]) / 10000)
    return max(1, remaining)
