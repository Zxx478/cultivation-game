"""突破：预览 / 执行 / 失败惩罚 / 根基 / 寿元重算。

数值全部来自 ``domain.realm`` 与 ``game.yaml.breakthrough``：
- 成功率 = 基础 + 体质 + 丹药 + 护法 + 吉日 + 灵地 + 连败补偿，clamp 5%–95%；
- 四类准备合计 ≤ +30%；5 连败后第 6 次必成；
- 失败按成功率三档惩罚，中伤写 ``injured``、重伤可走火入魔；
- 成功整体重算 ``lifespan_max_days``，并记录根基（圆满/扎实/虚浮/崩坏）。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..content import get_registry
from ..core.clock import DAYS_PER_YEAR, action_days
from ..core.errors import ACTION_INVALID, CULTIVATION_INSUFFICIENT, GameError
from ..core.rng import RngService
from ..db.game_repository import CharacterRepository, FlagRepository, InventoryRepository
from ..db.repository import atomic
from ..domain import attrs as attrs_domain
from ..domain import level as level_domain
from ..domain import realm as realm_domain
from .context import active_char, writable_char
from . import main_quest_service
from .save_service import SaveService

FAIL_FLAG = "last_breakthrough_fail"


def _realm_bundle(char: Mapping[str, Any]) -> Dict[str, Any]:
    """当前境界 + 下一境界 + 突破前置信息。"""
    registry = get_registry()
    current = registry.realm_by_index(int(char["realm_index"]))
    order = int(current["order"])
    try:
        nxt = registry.realm_by_index(order + 1)
    except Exception:
        nxt = None
    return {"current": current, "next": nxt, "registry": registry}


def _prep(req: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """规整冲关准备参数。"""
    prep = dict(req or {})
    return {
        "pill_count": max(0, min(2, int(prep.get("pill_count", 0) or 0))),
        "guardian": bool(prep.get("guardian", False)),
        "omen": bool(prep.get("omen", False)),
        "land": bool(prep.get("land", False)),
        "expected_version": prep.get("expected_version"),
    }


def _base_context(char: Mapping[str, Any]) -> Dict[str, Any]:
    """突破前置校验 + 公共上下文。"""
    bundle = _realm_bundle(char)
    current, nxt, registry = bundle["current"], bundle["next"], bundle["registry"]
    if nxt is None or not bool(nxt.get("enabled")):
        raise GameError(ACTION_INVALID, "此境未开，无法突破",
                        {"realm_index": int(char["realm_index"])})
    need = level_domain.exp_need(int(char["level"]), registry.exp_k_bp(int(char["level"])))
    if int(char["level"]) < int(current["level_end"]):
        raise GameError(CULTIVATION_INSUFFICIENT,
                        f"需先修至 Lv.{int(current['level_end'])}",
                        {"level": int(char["level"])})
    if int(char["cultivation"]) < need:
        raise GameError(CULTIVATION_INSUFFICIENT, "修为未满",
                        {"cultivation": int(char["cultivation"]), "need": need})
    last_fail = FlagRepository.get(int(char["id"]), FAIL_FLAG)
    last_fail_age = int(last_fail) if last_fail else None
    realm_domain.check_prereq(
        level=int(char["level"]), realm_level_end=int(current["level_end"]),
        cultivation=int(char["cultivation"]), exp_k_bp=registry.exp_k_bp(int(char["level"])),
        age_days=int(char["age_days"]), lifespan_max_days_value=int(char["lifespan_max_days"]),
        last_fail_age_days=last_fail_age, cfg=registry.breakthrough_config())
    return {"bundle": bundle, "need": need}


def preview(req: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """突破成功率明细（逐项 Δ + 最终值 + 惩罚预告）。"""
    char = active_char()
    ctx = _base_context(char)
    current, nxt, registry = ctx["bundle"]["current"], ctx["bundle"]["next"], ctx["bundle"]["registry"]
    prep = _prep(req)
    prep["pill_count"] = min(prep["pill_count"], _pill_count(int(char["id"])))
    physique = registry.physique(str(char.get("physique") or "fan_ti"))
    result = realm_domain.preview_breakthrough(
        int(current["breakthrough_base_bp"]),
        physique_bp=int(physique.get("breakthrough_bp", 0)),
        foundation_bp=realm_domain.foundation_next_bp(
            char.get("foundation"), registry.breakthrough_config()),
        pill_count=prep["pill_count"], guardian=prep["guardian"],
        omen=prep["omen"], land=prep["land"],
        fail_streak=int(char.get("fail_streak", 0)),
        cfg=registry.breakthrough_config())
    penalty = realm_domain.penalty_of(result.final_bp, registry.breakthrough_config())
    return {
        **result.as_dict(),
        "from_realm": str(current["display_name"]),
        "to_realm": str(nxt["display_name"]),
        "exp_need": int(ctx["need"]),
        "penalty": penalty,
        "lifespan_before_years": round((int(char["lifespan_max_days"]) - int(char["age_days"])) / DAYS_PER_YEAR, 1),
        "lifespan_after_years": int(nxt["base_lifespan_years"]),
        "pill_available": _pill_count(int(char["id"])),
    }


def _pill_count(char_id: int) -> int:
    """背包中的破境丹数量。"""
    pill_id = str(get_registry().breakthrough_config().get("pill_item", "po_jing_dan"))
    row = InventoryRepository.get(char_id, pill_id)
    return int(row["qty"]) if row else 0


@atomic
def execute(req: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """执行突破。"""
    char = writable_char()
    ctx = _base_context(char)
    current, nxt, registry = ctx["bundle"]["current"], ctx["bundle"]["next"], ctx["bundle"]["registry"]
    prep = _prep(req)
    # 消耗破境丹（最多 2 颗，实际有几颗用几颗）
    pills = min(prep["pill_count"], _pill_count(int(char["id"])))
    if pills:
        pill_id = str(registry.breakthrough_config().get("pill_item", "po_jing_dan"))
        InventoryRepository.remove(int(char["id"]), pill_id, pills)

    physique = registry.physique(str(char.get("physique") or "fan_ti"))
    result = realm_domain.preview_breakthrough(
        int(current["breakthrough_base_bp"]),
        physique_bp=int(physique.get("breakthrough_bp", 0)),
        foundation_bp=realm_domain.foundation_next_bp(
            char.get("foundation"), registry.breakthrough_config()),
        pill_count=pills, guardian=prep["guardian"],
        omen=prep["omen"], land=prep["land"],
        fail_streak=int(char.get("fail_streak", 0)),
        cfg=registry.breakthrough_config())

    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    success = True if result.guaranteed else rng.roll_bp(result.final_bp, stream="breakthrough")
    days = action_days(int(registry.action_base_days("breakthrough")), int(char["realm_index"]))
    attempts = int(char.get("realm_attempts", 0)) + 1
    fields: Dict[str, Any] = {
        "age_days": int(char["age_days"]) + days,
        "realm_attempts": attempts,
        "rng_counter": int(rng.counter) + 1,
    }
    conf = registry.breakthrough_config()

    if success:
        new_level = int(char["level"]) + 1
        foundation = realm_domain.foundation_of(attempts, conf)
        fields.update({
            "level": new_level,
            "realm_index": level_domain.realm_index_of(new_level),
            "realm_stage": level_domain.realm_stage_of(new_level),
            "cultivation": 0,
            "exp_overflow": int(char.get("exp_overflow", 0)),
            "fail_streak": 0,
            "realm_attempts": 0,
            "foundation": str(foundation["name"]),
            "lifespan_max_days": realm_domain.lifespan_max_days(int(nxt["base_lifespan_years"])),
            "hp": 0,
            "mp": 0,
            "status": "normal",
        })
        # 根基属性加成（全属性百分比）
        bonus_bp = int(foundation["attr_bonus_bp"])
        if bonus_bp:
            for key in ("str", "con", "spr", "sen", "bon", "agi"):
                fields[key] = int(int(char.get(key, 0)) * (10000 + bonus_bp) / 10000)
        FlagRepository.set(int(char["id"]), FAIL_FLAG, "")
    else:
        tier = realm_domain.penalty_of(result.final_bp, conf)
        fields["cultivation"] = realm_domain.punish_cultivation(int(char["cultivation"]), result.final_bp, conf)
        from .stats_service import build as build_stats
        stats = build_stats(char)
        fields["hp"] = realm_domain.punish_hp(stats.derived.hp_max, result.final_bp, conf)
        fields["fail_streak"] = int(char.get("fail_streak", 0)) + 1
        fields["dao_heart"] = max(0, int(char.get("dao_heart", 10)) + int(tier["dao_heart"]))
        if str(tier["name"]) == "medium":
            fields["status"] = "injured"
        elif str(tier["name"]) == "heavy":
            if rng.roll_bp(int(tier["qi_deviation_bp"]), stream="breakthrough:qi"):
                fields["status"] = "qi_deviation"
        base_age = int(char["age_days"]) + days
        fields["age_days"] = base_age + int(conf.get("cooldown_days", 30))
        FlagRepository.set(int(char["id"]), FAIL_FLAG, str(base_age))

    expected = prep.get("expected_version")
    CharacterRepository.update(
        int(char["id"]), fields,
        expected_version=int(expected) if expected is not None else int(char["version"]))
    if success:
        main_quest_service.advance(int(char["id"]), {"type": "breakthrough",
                                                     "realm_index": int(fields["realm_index"]),
                                                     "count": 1})
    SaveService.autosave(int(char["id"]))
    return {
        "success": bool(success),
        "final_bp": int(result.final_bp),
        "guaranteed": bool(result.guaranteed),
        "preview": result.as_dict(),
        "penalty": realm_domain.penalty_of(result.final_bp, conf) if not success else None,
        "foundation": realm_domain.foundation_of(attempts, conf),
        "level": int(fields.get("level", char["level"])),
        "realm_index": int(fields.get("realm_index", char["realm_index"])),
        "age_days": int(fields["age_days"]),
        "attempts": attempts,
    }
