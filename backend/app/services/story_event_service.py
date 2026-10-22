"""事件结算：选项判定 → 效果落地 → 清除待决事件。

待决事件以 JSON 存在 ``character.pending_event``：
``{instance_id, event_id, created_at_days}``。提交时必须同时匹配
``instance_id`` 与选项白名单，重复提交/换页重放一律 4011。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from ..content import get_registry
from ..core.errors import EVENT_OPTION_INVALID, NO_PENDING_EVENT, GameError
from ..core.rng import RngService
from ..db.game_repository import (
    CharacterRepository,
    FlagRepository,
    InventoryRepository,
    LogRepository,
    RelationRepository,
    TechniqueRepository,
)
from ..db.repository import SansxiuWalletRepository, atomic
from ..db.repository import SansxiuWalletRepository
from ..domain import attrs as attrs_domain
from ..domain import event as event_domain
from ..domain import level as level_domain
from . import main_quest_service
from .context import active_char
from .save_service import SaveService


def _load_pending(char: Mapping[str, Any]) -> Dict[str, Any]:
    """读取待决事件；无则 4010。"""
    raw = char.get("pending_event")
    if not raw:
        raise GameError(NO_PENDING_EVENT, "当前没有待处理的事件")
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        raise GameError(NO_PENDING_EVENT, "待决事件数据损坏")
    if not isinstance(data, dict) or not data.get("event_id"):
        raise GameError(NO_PENDING_EVENT, "待决事件数据损坏")
    return data


def _personality_mod(char: Mapping[str, Any], choice: Mapping[str, Any]) -> int:
    """性格对判定的修正（硬闯/智取/交涉/退避）。"""
    try:
        person = get_registry().personality(str(char.get("personality") or "zhongyong"))
    except Exception:
        return 0
    check = dict(choice.get("check") or {})
    attr = str(check.get("attr") or "")
    style = str(choice.get("style") or ("wits" if attr in ("sen", "wis") else "talk" if attr == "luk" else "hard"))
    return int(dict(person.get("check_mod_bp") or {}).get(style, 0))


@atomic
def choose(instance_id: str, option_id: str) -> Dict[str, Any]:
    """结算一个事件选项。

    Raises:
        GameError: 4010 无待决事件 / 4011 实例或选项非法。
    """
    char = active_char(alive=True)
    pending = _load_pending(char)
    if str(pending.get("instance_id")) != str(instance_id):
        raise GameError(EVENT_OPTION_INVALID, "事件实例已失效，请刷新后重试",
                        {"instance_id": instance_id})

    registry = get_registry()
    try:
        event = registry.event(str(pending["event_id"]))
    except Exception:
        raise GameError(4025, f"事件不存在：{pending.get('event_id')}")
    try:
        choice = event_domain.find_choice(event, str(option_id))
    except KeyError:
        raise GameError(EVENT_OPTION_INVALID, f"非法选项：{option_id}")

    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    result = event_domain.resolve_choice(
        choice, attrs={k: int(char.get(k, 0)) for k in attrs_domain.BASE_KEYS},
        personality_mod_bp=_personality_mod(char, choice),
        karma=int(char.get("karma", 0)), rng=rng,
        institute_stream=f"event:{event['id']}", cfg=registry.event_config())
    effects = dict(result.get("effects") or {})

    fields: Dict[str, Any] = {"pending_event": ""}
    deltas: Dict[str, Any] = {"success": bool(result["success"]),
                              "chance_bp": int(result["chance_bp"]),
                              "effects": effects}

    # ---- 时间 ----
    time_days = int(result.get("time_cost_days", 0) or 0)
    if time_days:
        fields["age_days"] = int(char["age_days"]) + time_days
        deltas["time_days"] = time_days

    # ---- 属性 ----
    caps = registry.attrs_config().get("soft_cap", {})
    for key, delta in dict(effects.get("attr") or {}).items():
        if key in attrs_domain.BASE_KEYS:
            current = int(char.get(key, 0))
            fields[key] = attrs_domain.apply_soft_cap(key, current + int(delta), caps)
    # ---- 修为 ----
    cult = int(effects.get("cultivation", 0) or 0)
    if cult:
        realm_end = int(registry.realm_of_level(int(char["level"]))["level_end"])
        applied = level_domain.apply_exp(
            int(char["level"]), int(char["cultivation"]), int(char["exp_overflow"]),
            cult, lambda lv: registry.exp_k_bp(lv), cap_level=realm_end)
        fields.update({"cultivation": applied.cultivation,
                       "exp_overflow": applied.exp_overflow,
                       "level": applied.level,
                       "realm_index": level_domain.realm_index_of(applied.level),
                       "realm_stage": level_domain.realm_stage_of(applied.level)})
        deltas["cultivation"] = cult
    # ---- 灵石 / 因果 / 道心 ----
    if effects.get("spirit_stone"):
        fields["spirit_stone"] = max(0, int(char["spirit_stone"]) + int(effects["spirit_stone"]))
        deltas["spirit_stone"] = int(effects["spirit_stone"])
    if effects.get("karma"):
        fields["karma"] = max(-1000, min(1000, int(char["karma"]) + int(effects["karma"])))
        deltas["karma"] = int(effects["karma"])
    if effects.get("dao_heart"):
        fields["dao_heart"] = max(0, min(100, int(char["dao_heart"]) + int(effects["dao_heart"])))
        deltas["dao_heart"] = int(effects["dao_heart"])
    # ---- 气血 / 灵力 ----
    if effects.get("hp_pct"):
        from .stats_service import build as build_stats
        stats = build_stats(char)
        max_hp = max(1, int(stats.derived.hp_max))
        current_hp = int(stats.hp)
        fields["hp"] = max(1, min(max_hp, int(current_hp * (100 + int(effects["hp_pct"])) / 100)))
        deltas["hp_pct"] = int(effects["hp_pct"])
    if effects.get("mp_pct"):
        from .stats_service import build as build_stats
        stats = build_stats(char)
        max_mp = max(1, int(stats.derived.mp_max))
        fields["mp"] = max(0, min(max_mp, int(stats.mp * (100 + int(effects["mp_pct"])) / 100)))
        deltas["mp_pct"] = int(effects["mp_pct"])
    if effects.get("statuses"):
        for status in effects["statuses"]:
            if status in ("injured", "qi_deviation"):
                fields["status"] = status
                deltas["status"] = status

    # ---- 物品 ----
    for item_id, qty in dict(effects.get("items_gained") or {}).items():
        try:
            item = registry.item(str(item_id))
        except Exception:
            continue
        InventoryRepository.add(int(char["id"]), str(item_id), str(item["kind"]), int(qty))
        deltas.setdefault("items_gained", {})[str(item_id)] = int(qty)
    for item_id, qty in dict(effects.get("items_lost") or {}).items():
        InventoryRepository.remove(int(char["id"]), str(item_id), int(qty))
        deltas.setdefault("items_lost", {})[str(item_id)] = int(qty)

    # ---- 功法 ----
    for tech_id in effects.get("techniques") or ():
        try:
            tech = registry.technique(str(tech_id))
        except Exception:
            continue
        if int(tech.get("required_realm_index", 1)) <= int(char["realm_index"]):
            TechniqueRepository.upsert(int(char["id"]), str(tech_id), layer=1)
            deltas.setdefault("techniques", []).append(str(tech_id))

    # ---- 标记 / 好感 ----
    for key in effects.get("flags") or ():
        FlagRepository.set(int(char["id"]), str(key), "1")
        deltas.setdefault("flags", []).append(str(key))
    for npc_id, favor in dict(effects.get("favors") or {}).items():
        RelationRepository.add_favor(int(char["id"]), str(npc_id), int(favor), 0)

    fields["rng_counter"] = int(rng.counter) + 1
    CharacterRepository.update(int(char["id"]), fields,
                               expected_version=int(char["version"]))
    FlagRepository.set(int(char["id"]), f"event_last:{event['id']}", str(fields.get("age_days", char["age_days"])))
    LogRepository.add_event(int(char["id"]), "event",
                            {"event_id": event["id"], "choice": option_id,
                             "success": bool(result["success"]), "deltas": deltas})
    main_quest_service.advance(int(char["id"]), {"type": "event_choice",
                                                 "event_id": str(event["id"]),
                                                 "count": 1})
    SaveService.autosave(int(char["id"]))
    return {
        "event_id": str(event["id"]),
        "choice_id": str(option_id),
        "success": bool(result["success"]),
        "chance_bp": int(result["chance_bp"]),
        "time_days": time_days,
        "deltas": deltas,
        "title": str(event.get("title", "")),
    }
