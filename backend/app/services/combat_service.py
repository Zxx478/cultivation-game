"""战斗会话：开战 / 回合 / 收尾奖励。

战斗引擎是纯函数（``domain.combat``），本服务负责：
- 读取会话并校验归属（一角色一会话，刷新可恢复）；
- 把前端指令翻译成引擎 action（技能/道具/逃跑）；
- 胜利后发奖、写击杀标记、推进主线；
- 失败时消耗九转还魂丹或置 ``dead``；
- 禁地 Boss 胜利后回调宗门桥接层（扣贡献/首通/发功法）。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..content import get_registry
from ..core.errors import (
    COMBAT_ACTION_INVALID,
    COMBAT_ENDED,
    COMBAT_NOT_FOUND,
    ITEM_INSUFFICIENT,
    GameError,
)
from ..core.rng import RngService
from ..db.game_repository import (
    CharacterRepository,
    CombatSessionRepository,
    FlagRepository,
    InventoryRepository,
    LogRepository,
)
from ..db.repository import atomic
from ..domain import combat as combat_domain
from . import main_quest_service
from .context import active_char
from .grant_service import GrantService
from .save_service import SaveService


def _view(row: Mapping[str, Any]) -> Dict[str, Any]:
    """会话 → 前端视图。"""
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
        "skills": list((session.get("player") or {}).get("skills") or []),
    }


def _require_session(char_id: int) -> Dict[str, Any]:
    """取当前会话；不存在 4012。"""
    row = CombatSessionRepository.get(char_id)
    if row is None:
        raise GameError(COMBAT_NOT_FOUND, "没有进行中的战斗")
    if int(row.get("char_id", char_id)) != int(char_id):
        raise GameError(4013, "战斗会话不属于当前角色")
    if str(row.get("status")) != "active":
        raise GameError(COMBAT_ENDED, "战斗已结束")
    return row


def current() -> Optional[Dict[str, Any]]:
    """当前战斗（无则 None）。"""
    char = active_char()
    row = CombatSessionRepository.get(int(char["id"]))
    return _view(row) if row else None


def _consume_battle_item(char_id: int, item_id: str) -> Dict[str, Any]:
    """扣除战斗道具并返回引擎 action。"""
    registry = get_registry()
    try:
        item = registry.item(str(item_id))
    except Exception:
        raise GameError(COMBAT_ACTION_INVALID, f"未知道具：{item_id}")
    inv = InventoryRepository.get(char_id, str(item_id))
    if inv is None or int(inv.get("qty", 0)) <= 0:
        raise GameError(ITEM_INSUFFICIENT, f"没有 {item.get('name', item_id)}")
    InventoryRepository.remove(char_id, str(item_id), 1)
    effect = str(item.get("effect"))
    params = dict(item.get("params") or {})
    if effect == "restore_hp":
        return {"type": "item", "heal_pct": int(params.get("hp_pct", 30))}
    if effect == "restore_mp":
        return {"type": "item", "mp_pct": int(params.get("mp_pct", 50))}
    if effect == "flee_guaranteed":
        return {"type": "flee", "guaranteed": True}
    raise GameError(COMBAT_ACTION_INVALID, f"{item.get('name', item_id)} 无法在战斗中使用")


def _roll_drops(char_id: int, drops: Any, rng: RngService) -> Dict[str, int]:
    """按掉率抽战利品。"""
    got: Dict[str, int] = {}
    for drop in drops or ():
        if not isinstance(drop, Mapping):
            continue
        rate = int(drop.get("rate_bp", 0))
        if rate > 0 and rng.roll_bp(rate, stream="combat:drop"):
            item_id = str(drop.get("item_id"))
            qty = int(drop.get("qty", 1))
            got[item_id] = got.get(item_id, 0) + qty
    return got


def _persist_vitals(char_id: int, session: Mapping[str, Any]) -> None:
    """把战斗结束时的气血/灵力写回角色（否则伤害不持久）。"""
    player = dict(session.get("player") or {})
    if not player:
        return
    char = CharacterRepository.require(char_id)
    CharacterRepository.update(char_id, {
        "hp": max(0, int(player.get("hp", 0))),
        "mp": max(0, int(player.get("mp", 0))),
    }, expected_version=int(char["version"]))


@atomic
def _after_victory(char_id: int, row: Mapping[str, Any], session: Mapping[str, Any],
                   rng: RngService) -> Dict[str, Any]:
    """胜利结算：修为/灵石/掉落/击杀/主线/禁地。

    - 敌人逃跑：只发一半经验，不发灵石/掉落，也不计击杀；
    - 先删除战斗会话，再结算禁地，避免回调异常把玩家卡在「战斗未结束」。
    """
    meta = dict(session.get("meta") or {})
    rewards = dict(meta.get("rewards") or {})
    flags = dict(session.get("flags") or {})
    enemy_fled = bool(flags.get("enemy_fled"))
    _persist_vitals(char_id, session)
    char = CharacterRepository.require(char_id)
    enemy_id = str(meta.get("enemy_id") or "")
    session_id = str(meta.get("session_id") or f"{char_id}:{char['age_days']}")

    if enemy_fled:
        drops: Dict[str, int] = {}
        payload = {"exp": int(rewards.get("exp", 0)) // 2, "stones": 0, "items": []}
    else:
        drops = _roll_drops(char_id, rewards.get("drops"), rng)
        payload = {
            "exp": int(rewards.get("exp", 0)),
            "stones": int(rewards.get("stones", 0)),
            "items": [{"item_id": k, "qty": v} for k, v in drops.items()],
        }

    grant = GrantService.grant(char_id, payload, source_type="combat",
                               source_ref=f"combat:{session_id}")
    if enemy_id and not enemy_fled:
        key = f"kill:{enemy_id}"
        try:
            count = int(FlagRepository.get(char_id, key, "0") or 0) + 1
        except (TypeError, ValueError):
            count = 1
        FlagRepository.set(char_id, key, str(count))
        main_quest_service.advance(char_id, {"type": "kill", "enemy_id": enemy_id, "count": 1})
    LogRepository.add_event(char_id, "combat",
                            {"result": "enemy_fled" if enemy_fled else "victory",
                             "enemy_id": enemy_id, "grant": grant})
    result: Dict[str, Any] = {"result": "victory", "enemy_id": enemy_id,
                              "enemy_fled": enemy_fled, "drops": drops, "grant": grant}

    # 先结束会话：禁地回调异常也不能让玩家卡在「战斗尚未结束」
    CombatSessionRepository.delete(char_id)

    if str(meta.get("kind")) == "forbidden" and meta.get("extra", {}).get("land_id"):
        from .sect_bridge_service import complete_forbidden
        land_id = str(meta["extra"]["land_id"])
        try:
            result["forbidden"] = complete_forbidden(
                char_id, land_id, int(meta["extra"].get("attempt", 1)))
        except Exception as exc:  # 结算失败不阻断主流程，记录待补
            result["forbidden_error"] = str(exc)
            LogRepository.add_event(char_id, "forbidden_error",
                                    {"land_id": land_id, "error": str(exc)})

    SaveService.autosave(char_id)
    return result


@atomic
def _after_defeat(char_id: int, session: Mapping[str, Any]) -> Dict[str, Any]:
    """失败结算：还魂丹续战，否则置 dead。"""
    inv = InventoryRepository.get(char_id, "jiuzhuan_huanshun")
    if inv is not None and int(inv.get("qty", 0)) > 0:
        InventoryRepository.remove(char_id, "jiuzhuan_huanshun", 1)
        revived = combat_domain.apply_revive(session, hp_pct=50)
        _persist_vitals(char_id, revived)
        CombatSessionRepository.save(char_id, revived, status=str(revived["status"]),
                                     turn=int(revived["turn"]))
        return {"result": "defeat", "revived": True, "combat": _view({
            "session": revived, "status": revived["status"], "turn": revived["turn"]})}
    CharacterRepository.update(char_id, {"status": "dead", "pending_event": "", "hp": 0})
    LogRepository.add_event(char_id, "combat", {"result": "defeat"})
    CombatSessionRepository.delete(char_id)
    SaveService.autosave(char_id)
    return {"result": "defeat", "revived": False, "dead": True}


@atomic
def act(action_type: str, *, skill_id: Optional[str] = None,
        item_id: Optional[str] = None) -> Dict[str, Any]:
    """执行一回合。

    Args:
        action_type: attack / skill / defend / item / flee。
        skill_id: ``action_type=skill`` 时必填。
        item_id: ``action_type=item`` 时必填。

    Raises:
        GameError: 4012/4014/4026/4016。
    """
    char = active_char(alive=True)
    row = _require_session(int(char["id"]))
    session = dict(row["session"])
    kind = str(action_type or "")
    if kind == "skill" and not skill_id:
        raise GameError(COMBAT_ACTION_INVALID, "缺少 skill_id")
    if kind == "item":
        if not item_id:
            raise GameError(COMBAT_ACTION_INVALID, "缺少 item_id")
        action = _consume_battle_item(int(char["id"]), str(item_id))
    else:
        action = {"type": kind, "skill_id": skill_id}

    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    new_state = combat_domain.step(session, action, rng,
                                   cfg=get_registry().combat_config())
    CharacterRepository.update(int(char["id"]), {"rng_counter": int(rng.counter) + 1},
                               expected_version=int(char["version"]))
    status = str(new_state["status"])
    CombatSessionRepository.save(int(char["id"]), new_state, status=status,
                                 turn=int(new_state["turn"]))

    if status == "active":
        return {"combat": _view({"session": new_state, "status": status,
                                 "turn": new_state["turn"]})}
    if status == "victory":
        return {"combat": _view({"session": new_state, "status": status,
                                 "turn": new_state["turn"]}),
                "settle": _after_victory(int(char["id"]), row, new_state, rng)}
    if status == "defeat":
        return {"combat": _view({"session": new_state, "status": status,
                                 "turn": new_state["turn"]}),
                "settle": _after_defeat(int(char["id"]), new_state)}
    # escaped / timeout：结束会话，保留气血灵力
    _persist_vitals(int(char["id"]), new_state)
    CombatSessionRepository.delete(int(char["id"]))
    LogRepository.add_event(int(char["id"]), "combat", {"result": status})
    SaveService.autosave(int(char["id"]))
    return {"combat": _view({"session": new_state, "status": status,
                             "turn": new_state["turn"]}),
            "settle": {"result": status}}
