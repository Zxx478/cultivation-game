"""行动结算：闭关 / 历练 / 采药 / 切磋 / 打坐 / 持续闭关。

一次请求 = 一段游戏时间，立即结算（任务书 §2.4 / 系统设计 §4.2）：
1. 守卫：已坐化 / 待决事件 / 进行中的战斗都拒绝新行动；
2. ``days = action_days(base, realm_index)``，锻血闭关 30 日、辟府 42 日；
3. 闭关产出 = ``ExpRate × days``（内部 float，只 floor 一次）；
4. 修为升级受「本境等级上限」约束（Lv.9 必须突破才能进 Lv.10）；
5. 每次行动后 35% 触发事件（散修 38.5%），事件会中断持续闭关；
6. 历练/切磋生成战斗会话，胜负与奖励由 ``combat_service`` 收尾；
7. 同一事务内推进 ``age_days`` 与 ``rng_counter``，并覆盖自动存档。
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.clock import action_days, game_year
from ..core.errors import (
    ACTION_INVALID,
    COMBAT_NOT_FOUND,
    LIFESPAN_INSUFFICIENT,
    NO_PENDING_EVENT,
    GameError,
)
from ..core.rng import RngService
from ..db.game_repository import (
    CharacterRepository,
    CombatSessionRepository,
    FlagRepository,
    InventoryRepository,
    LogRepository,
    QuestRepository,
)
from ..domain import attrs as attrs_domain
from ..domain import combat as combat_domain
from ..domain import event as event_domain
from ..domain import level as level_domain
from . import main_quest_service, stats_service
from ..db.repository import atomic
from .context import active_char
from .grant_service import GrantService
from .save_service import SaveService

ACTION_ALIASES = {
    "closed_door_meditation": "seclusion",
    "closed_door": "seclusion",
    "retreat": "seclusion",
    "herb_gathering": "gather",
    "sparring": "spar",
    "meditation_heal": "meditate",
}


def _normalize_kind(kind: str) -> str:
    """把旧命名/别名归一到 game.yaml 的 5 类行动。"""
    value = ACTION_ALIASES.get(str(kind), str(kind))
    if value not in ("seclusion", "adventure", "gather", "spar", "meditate"):
        raise GameError(ACTION_INVALID, f"未知行动：{kind}")
    return value


def _dumps(value: Any) -> str:
    """紧凑 JSON。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _plain(value: Any) -> Any:
    """把冻结内容（MappingProxy / tuple）转成可 JSON 序列化的原生结构。"""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def _level_growth(char: Mapping[str, Any], old_level: int, new_level: int) -> Dict[str, int]:
    """跨级属性成长（每小境按 realms.per_level_growth 累加）。"""
    registry = get_registry()
    out: Dict[str, int] = {}
    for lv in range(int(old_level) + 1, int(new_level) + 1):
        realm = registry.realm_of_level(lv)
        for key, value in dict(realm.get("per_level_growth") or {}).items():
            if key in attrs_domain.BASE_KEYS:
                out[key] = out.get(key, 0) + int(value)
    return out


def _event_last_seen(char_id: int) -> Dict[str, int]:
    """读取事件去重时间（``event_last:<id>`` → age_days）。"""
    out: Dict[str, int] = {}
    for key, value in FlagRepository.all_for(char_id).items():
        if key.startswith("event_last:"):
            try:
                out[key.split(":", 1)[1]] = int(value)
            except (TypeError, ValueError):
                pass
    return out


def _forced_event(char_id: int) -> Optional[Mapping[str, Any]]:
    """当前主线强制事件（如 quest_06 的村里哭声）。"""
    registry = get_registry()
    for row in QuestRepository.list_for(char_id):
        if str(row.get("status")) not in ("active", "ready"):
            continue
        try:
            quest = registry.quest(str(row["quest_id"]))
        except Exception:
            continue
        for obj in quest.get("objectives", ()):
            if str(obj.get("type")) == "event_choice":
                event_id = str(obj.get("event_id") or "")
                progress = (row.get("progress") or {}).get("0", 0)
                if event_id and int(progress) < 1:
                    try:
                        return registry.event(event_id)
                    except Exception:
                        return None
    return None


def _pick_event(char_id: int, char: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """抽取一条事件（含主线强制与 30 月去重）。"""
    registry = get_registry()
    forced = _forced_event(char_id)
    if forced is not None:
        return forced
    region_id = str(char.get("region_id") or "")
    level = int(char["level"])
    events = [e for e in registry.events_for(region_id, level)
              if not e.get("forced_by_quest")]
    seen = _event_last_seen(char_id)
    now = int(char["age_days"])
    dedupe_days = int(registry.event_config().get("dedupe_days", 1800))
    pool = []
    for event in events:
        last = seen.get(str(event["id"]))
        if last is not None and now - int(last) < dedupe_days:
            continue
        weight = registry.category_weight_bp(str(event["category"])) * int(event.get("weight", 0))
        if weight > 0:
            pool.append((event, weight))
    if not pool:
        # 池子耗尽时允许重复（保底不空转）
        pool = [(e, registry.category_weight_bp(str(e["category"])) * int(e.get("weight", 0)))
                for e in events]
        pool = [(e, w) for e, w in pool if w > 0]
    if not pool:
        return None
    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    return rng.weighted_pick(pool, stream="event:pick")


def _event_payload(char: Mapping[str, Any], event: Mapping[str, Any]) -> Dict[str, Any]:
    """生成待决事件快照（不含成功率）。"""
    return {
        "instance_id": secrets.token_hex(8),
        "event_id": str(event["id"]),
        "created_at_days": int(char["age_days"]),
    }


def _event_view(payload: Mapping[str, Any], event: Mapping[str, Any]) -> Dict[str, Any]:
    """待决事件 → 前端视图。"""
    return {
        "instance_id": str(payload["instance_id"]),
        "event_id": str(payload["event_id"]),
        "title": str(event.get("title", "")),
        "body": str(event.get("body", "")),
        "options": [{
            "id": str(c.get("id")),
            "label": str(c.get("label")),
            "risk": str(c.get("risk", "normal")),
            "time_cost_days": int(c.get("time_cost_days", 0)),
        } for c in event.get("choices", ())],
    }


def _combat_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    """战斗会话 → 前端视图。"""
    session = dict(row.get("session") or {})
    return {
        "status": str(row.get("status", "active")),
        "turn": int(row.get("turn", 1)),
        "max_turns": int(session.get("max_turns", 30)),
        "player": dict(session.get("player") or {}),
        "enemy": dict(session.get("enemy") or {}),
        "flags": dict(session.get("flags") or {}),
        "log": list(session.get("log") or [])[-20:],
        "is_boss": bool((session.get("flags") or {}).get("is_boss")),
    }


def _start_combat(char_id: int, char: Mapping[str, Any], *,
                  enemy_id: str, is_boss: bool = False, is_secret: bool = False,
                  kind: str = "adventure", extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """创建战斗会话并返回视图。"""
    registry = get_registry()
    enemy_row = registry.enemy(enemy_id)
    stats = stats_service.build(char_id)
    player = stats.combat_player()

    enemy = combat_domain.enemy_from_content(enemy_row)
    # 等级缩放：避免 Lv.1 玩家被 Lv.9 BOSS 一击秒杀（同阶 ±40%）
    ratio = float(char["level"]) / max(1.0, float(enemy_row.get("level", char["level"])))
    ratio = max(0.7, min(1.4, ratio))
    enemy["hp"] = max(1, int(int(enemy["hp"]) * ratio))
    enemy["hp_max"] = enemy["hp"]
    enemy["atk"] = float(enemy["atk"]) * ratio
    enemy["def"] = float(enemy["def"]) * ratio

    year = game_year(int(char["age_days"]))
    battle_key = f"battle_year:{year}"
    try:
        consec = int(FlagRepository.get(char_id, battle_key, "0") or 0) + 1
    except (TypeError, ValueError):
        consec = 1
    FlagRepository.set(char_id, battle_key, str(consec))

    state = combat_domain.build_state(
        player, enemy, is_boss=is_boss, is_secret=is_secret,
        consec_battle=consec, cfg=registry.combat_config())
    meta = {
        "session_id": secrets.token_hex(8),
        "kind": kind,
        "enemy_id": str(enemy_id),
        "region_id": str(char.get("region_id") or ""),
        "is_boss": bool(is_boss),
        "is_secret": bool(is_secret),
        "rewards": {
            "exp": int(enemy_row.get("exp_reward", 0)),
            "stones": int(enemy_row.get("stone_reward", 0)),
            "drops": _plain(list(enemy_row.get("drops") or [])),
        },
        "extra": _plain(dict(extra or {})),
    }
    state["meta"] = meta
    CombatSessionRepository.save(char_id, state, status=str(state["status"]), turn=int(state["turn"]))
    return _combat_view({"session": state, "status": state["status"], "turn": state["turn"]})


def _append_items(char_id: int, items: Dict[str, int]) -> Dict[str, int]:
    """把一批物品放入背包（按堆叠），返回实际入包数量。"""
    registry = get_registry()
    valid: List[str] = []
    for item_id, qty in items.items():
        if qty <= 0:
            continue
        try:
            registry.item(str(item_id))
        except Exception:
            continue
        valid.append(str(item_id))
    if valid:
        GrantService.ensure_bag_room(char_id, valid)
    got: Dict[str, int] = {}
    for item_id, qty in items.items():
        if qty <= 0:
            continue
        try:
            item = registry.item(str(item_id))
        except Exception:
            continue
        InventoryRepository.add(char_id, str(item_id), str(item["kind"]), int(qty))
        got[str(item_id)] = int(qty)
    return got


@atomic
def execute(kind: str, *, repeat: int = 1) -> Dict[str, Any]:
    """执行一次行动。

    Args:
        kind: seclusion / adventure / gather / spar / meditate。
        repeat: 持续闭关次数（1–10）；仅 seclusion 支持批量。

    Raises:
        GameError: 4005/4006/4010/4012。
    """
    kind = _normalize_kind(kind)
    repeat = max(1, min(10, int(repeat or 1)))
    if kind != "seclusion":
        repeat = 1

    char = active_char(alive=True)
    if str(char.get("pending_event") or ""):
        raise GameError(NO_PENDING_EVENT, "先处理眼前之事")
    if CombatSessionRepository.get(int(char["id"])) is not None:
        raise GameError(COMBAT_NOT_FOUND, "战斗尚未结束")

    registry = get_registry()
    base_days = registry.action_base_days(kind)
    total_days = 0
    total_exp = 0
    items_got: Dict[str, int] = {}
    level_before = int(char["level"])
    pending_event = None
    combat = None
    messages: List[str] = []

    for _ in range(repeat):
        char = CharacterRepository.require(int(char["id"]))
        days = action_days(base_days, int(char["realm_index"]))
        total_days += days
        fields: Dict[str, Any] = {"age_days": int(char["age_days"]) + days}
        rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))

        if kind == "seclusion":
            gain, stats = stats_service.exp_gain(int(char["id"]), days)
            realm_end = int(registry.realm_of_level(int(char["level"]))["level_end"])
            result = level_domain.apply_exp(
                int(char["level"]), int(char["cultivation"]), int(char["exp_overflow"]),
                gain, lambda lv: registry.exp_k_bp(lv), cap_level=realm_end)
            fields.update({
                "cultivation": result.cultivation,
                "exp_overflow": result.exp_overflow,
                "level": result.level,
                "realm_index": level_domain.realm_index_of(result.level),
                "realm_stage": level_domain.realm_stage_of(result.level),
            })
            if result.levels_gained:
                growth = _level_growth(char, int(char["level"]), result.level)
                for key, value in growth.items():
                    fields[key] = int(char.get(key, 0)) + int(value)
                fields.setdefault("hp", stats.derived.hp_max)
                fields.setdefault("mp", stats.derived.mp_max)
            total_exp += gain
            main_quest_service.advance(int(char["id"]), {"type": "cultivate", "count": 1})
            messages.append(f"闭关 {days} 日，得修为 {gain}")
        elif kind == "gather":
            materials = list(registry.region(str(char["region_id"])).get("materials") or ())
            if materials:
                picked = rng.pick(materials, stream="action:gather")
                qty = 1 + (1 if rng.roll_bp(2500, stream="action:gather:bonus") else 0)
                got = _append_items(int(char["id"]), {str(picked): qty})
                items_got.update(got)
                messages.append(f"采得 {picked} ×{qty}")
            else:
                messages.append("此地无药可采")
        elif kind == "meditate":
            stats = stats_service.build(char)
            heal = max(1, int(stats.derived.hp_max * 0.2))
            fields["hp"] = min(int(stats.derived.hp_max), int(stats.hp) + heal)
            fields["mp"] = int(stats.derived.mp_max)
            fields["status"] = "normal" if str(char.get("status")) == "injured" else char.get("status")
            messages.append(f"打坐 {days} 日，气血回复 {heal}")
        elif kind in ("adventure", "spar"):
            enemy_ids = list(registry.region(str(char["region_id"])).get("enemies") or ())
            if not enemy_ids:
                messages.append("此地安宁，无事可做")
            else:
                enemy_id = rng.pick(enemy_ids, stream=f"action:{kind}:enemy")
                fields["rng_counter"] = rng.counter + 1
                CharacterRepository.update(int(char["id"]), fields,
                                           expected_version=int(char["version"]))
                combat = _start_combat(int(char["id"]), char, enemy_id=str(enemy_id),
                                       kind=kind)
                SaveService.autosave(int(char["id"]))
                return {
                    "action": kind, "days": total_days,
                    "settle": {"total_days": total_days, "exp": total_exp,
                               "items": items_got, "level_before": level_before,
                               "level_after": int(char["level"]), "messages": messages},
                    "pending_event": None, "combat": combat,
                }

        # 事件判定（战斗/切磋不触发，避免双弹窗）
        if kind != "spar":
            if event_domain.roll_trigger(str(char.get("path_id") or ""), rng,
                                         cfg=registry.event_config()):
                event = _pick_event(int(char["id"]), char)
                if event is not None:
                    payload = _event_payload(char, event)
                    fields["rng_counter"] = rng.counter + 1
                    fields["pending_event"] = _dumps(payload)
                    CharacterRepository.update(int(char["id"]), fields,
                                               expected_version=int(char["version"]))
                    pending_event = _event_view(payload, event)
                    SaveService.autosave(int(char["id"]))
                    break

        fields["rng_counter"] = rng.counter + 1
        CharacterRepository.update(int(char["id"]), fields,
                                   expected_version=int(char["version"]))
        if pending_event:
            break

    SaveService.autosave(int(char["id"]))
    char_after = CharacterRepository.require(int(char["id"]))
    return {
        "action": kind,
        "days": total_days,
        "settle": {
            "total_days": total_days,
            "exp": total_exp,
            "items": items_got,
            "level_before": level_before,
            "level_after": int(char_after["level"]),
            "hp": int(char_after["hp"]),
            "mp": int(char_after["mp"]),
            "messages": messages,
        },
        "pending_event": pending_event,
        "combat": combat,
    }
