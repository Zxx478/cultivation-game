"""宗门任务与贡献系统配置加载与校验。

启动时由 load() 读取 data/content/sect/quest_config.yaml，校验后
冻结为不可变 dataclass 提供给 Service 层引用。
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------- 数据结构 ----------------


@dataclass(frozen=True)
class QuestLevelConfig:
    level_id: str
    display_name: str
    contribution_min: int
    contribution_max: int
    reputation_for_sansxiu_min: int
    reputation_for_sansxiu_max: int
    count_toward_unlock: bool
    daily_cap: int


@dataclass(frozen=True)
class DiscipleUnlockConfig:
    mid_required_low_quests: int
    high_required_mid_quests: int


@dataclass(frozen=True)
class SansxiuUnlockConfig:
    mid_required_reputation: int


@dataclass(frozen=True)
class SansxiuGeneralShareConfig:
    low: float
    mid: float
    high: float


@dataclass(frozen=True)
class ExchangeReputationThreshold:
    low_technique: int
    mid_technique: int
    high_technique: int


@dataclass(frozen=True)
class ForbiddenRewardEntry:
    type: str
    item_id: Optional[str] = None
    tech_id: Optional[str] = None
    mat_id: Optional[str] = None
    amount: int = 0
    drop_rate: float = 1.0


@dataclass(frozen=True)
class ForbiddenLandConfig:
    land_id: str
    display_name: str
    required_realm_seq: int
    sansxiu_bonus_reward_rate: float
    cost_contribution: int
    cost_lifespan_days: int
    boss_name: str
    boss_force_level_coef: float
    first_clear_rewards: List[ForbiddenRewardEntry]
    repeat_rewards: List[ForbiddenRewardEntry]
    sansxiu_extra_bonus: List[ForbiddenRewardEntry]


@dataclass(frozen=True)
class SectConfig:
    """宗门任务+贡献系统全局配置。"""
    valid_sect_ids: List[str]
    sansxiu_id: str
    reputation_max: int

    quest_levels: Dict[str, QuestLevelConfig]
    disciple_unlock: DiscipleUnlockConfig
    sansxiu_unlock: SansxiuUnlockConfig

    sansxiu_general_share: SansxiuGeneralShareConfig
    sansxiu_join_convert_rate: float
    sansxiu_exchange_reputation_required: ExchangeReputationThreshold
    sansxiu_min_reputation_to_take_quest: int

    forbidden_land_ids: List[str]
    forbidden_entries_per_game_year: int
    forbidden_cooldown_days: int
    forbidden_lands: Dict[str, ForbiddenLandConfig]


# ---------------- 加载与校验 ----------------


class ConfigValidationError(Exception):
    """配置文件校验失败。"""


def _parse_level(d: Dict[str, Any]) -> QuestLevelConfig:
    return QuestLevelConfig(
        level_id=d["level_id"],
        display_name=d["display_name"],
        contribution_min=int(d["contribution_min"]),
        contribution_max=int(d["contribution_max"]),
        reputation_for_sansxiu_min=int(d.get("reputation_for_sansxiu_min", 0)),
        reputation_for_sansxiu_max=int(d.get("reputation_for_sansxiu_max", 0)),
        count_toward_unlock=bool(d.get("count_toward_unlock", False)),
        daily_cap=int(d["daily_cap"]),
    )


def _parse_reward(d: Dict[str, Any]) -> ForbiddenRewardEntry:
    return ForbiddenRewardEntry(
        type=d["type"],
        item_id=d.get("item_id"),
        tech_id=d.get("tech_id"),
        mat_id=d.get("mat_id"),
        amount=int(d.get("amount", 0)),
        drop_rate=float(d.get("drop_rate", 1.0)),
    )


def _parse_land(d: Dict[str, Any]) -> ForbiddenLandConfig:
    return ForbiddenLandConfig(
        land_id=d["land_id"],
        display_name=d["display_name"],
        required_realm_seq=int(d["required_realm_seq"]),
        sansxiu_bonus_reward_rate=float(d["sansxiu_bonus_reward_rate"]),
        cost_contribution=int(d["cost_contribution"]),
        cost_lifespan_days=int(d.get("cost_lifespan_days", 0)),
        boss_name=d["boss"]["name"],
        boss_force_level_coef=float(d["boss"]["force_level_coef"]),
        first_clear_rewards=[_parse_reward(r) for r in d.get("rewards", {}).get("first_clear", [])],
        repeat_rewards=[_parse_reward(r) for r in d.get("rewards", {}).get("repeat", [])],
        sansxiu_extra_bonus=[_parse_reward(r) for r in d.get("rewards", {}).get("sansxiu_extra_bonus", [])],
    )


def _validate(cfg: SectConfig) -> None:
    # 1. 等级必含 low / mid / high
    for lvl in ("low", "mid", "high"):
        if lvl not in cfg.quest_levels:
            raise ConfigValidationError(f"quest.levels.{lvl} missing")
    # 2. 数值单调
    levels = cfg.quest_levels
    if not (levels["low"].contribution_max < levels["mid"].contribution_min
            and levels["mid"].contribution_max < levels["high"].contribution_min):
        raise ConfigValidationError(
            "quest.levels contribution ranges must be strictly increasing low<mid<high"
        )
    # 3. 散修不能解锁 high（隐含规则）：sansxiu_unlock 只有 mid_required_reputation，没有 high
    # 配置上已体现
    # 4. 兑换阈值单调
    ex = cfg.sansxiu_exchange_reputation_required
    if not (ex.low_technique < ex.mid_technique < ex.high_technique):
        raise ConfigValidationError("sansxiu_exchange_reputation_required must be monotonic")
    # 5. 禁地：MVP 至多 2 处（避免误启用）
    if len(cfg.forbidden_land_ids) > 2:
        raise ConfigValidationError(
            f"MVP 只允许 ≤ 2 处禁地，当前 {len(cfg.forbidden_land_ids)} 处"
        )


@functools.lru_cache(maxsize=1)
def load(config_path: Optional[str] = None) -> SectConfig:
    """加载配置文件，幂等，多次调用复用结果。

    Args:
        config_path: 配置文件绝对路径，默认为
            cultivation-game/data/content/sect/quest_config.yaml
    """
    if config_path is None:
        # 自动定位：相对 backend/app/config/loader.py 上四级
        here = Path(__file__).resolve()
        project_root = here.parents[3]
        config_path = str(project_root / "data" / "content" / "sect" / "quest_config.yaml")

    with open(config_path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = yaml.safe_load(f)

    sect = raw["sect"]
    quest = raw["quest"]
    contrib = raw.get("contribution", {})
    forb = raw.get("forbidden_land", {})

    levels = {k: _parse_level(v) for k, v in quest["levels"].items()}

    cfg = SectConfig(
        valid_sect_ids=list(sect["valid_sect_ids"]),
        sansxiu_id=sect["sansxiu_id"],
        reputation_max=int(sect["reputation_max"]),
        quest_levels=levels,
        disciple_unlock=DiscipleUnlockConfig(
            mid_required_low_quests=int(quest["disciple_unlock"]["mid_required_low_quests"]),
            high_required_mid_quests=int(quest["disciple_unlock"]["high_required_mid_quests"]),
        ),
        sansxiu_unlock=SansxiuUnlockConfig(
            mid_required_reputation=int(quest["sansxiu_unlock"]["mid_required_reputation"]),
        ),
        sansxiu_general_share=SansxiuGeneralShareConfig(
            low=float(contrib["sansxiu_general_share"]["low"]),
            mid=float(contrib["sansxiu_general_share"]["mid"]),
            high=float(contrib["sansxiu_general_share"]["high"]),
        ),
        sansxiu_join_convert_rate=float(contrib["sansxiu_join_convert_rate"]),
        sansxiu_exchange_reputation_required=ExchangeReputationThreshold(
            low_technique=int(contrib["sansxiu_exchange_reputation_required"]["low_technique"]),
            mid_technique=int(contrib["sansxiu_exchange_reputation_required"]["mid_technique"]),
            high_technique=int(contrib["sansxiu_exchange_reputation_required"]["high_technique"]),
        ),
        sansxiu_min_reputation_to_take_quest=int(contrib["sansxiu_min_reputation_to_take_quest"]),
        forbidden_land_ids=list(forb["enabled_ids"]),
        forbidden_entries_per_game_year=int(forb["entries_per_game_year"]),
        forbidden_cooldown_days=int(forb["cooldown_days"]),
        forbidden_lands={k: _parse_land(v) for k, v in forb["lands"].items()},
    )

    _validate(cfg)
    return cfg


def reload(config_path: Optional[str] = None) -> SectConfig:
    """测试用：清缓存重新加载。"""
    load.cache_clear()
    return load(config_path)
