"""主线任务：进度推进、自动激活、领取奖励。

- 增量类目标（闭关/击杀/宗门任务/奇遇/对话）走 ``advance``；
- 绝对类目标（等级/境界/持有物）在读取/领取时用 ``sync`` 幂等补齐；
- 领奖走 ``GrantService`` 的 ``quest:<id>`` 幂等键，重复领取 409/4029。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from ..content import get_registry
from ..core.errors import QUEST_NOT_READY, QUEST_UNKNOWN, GameError
from ..db.game_repository import (
    CharacterRepository,
    FlagRepository,
    InventoryRepository,
    QuestRepository,
)
from ..domain import quest as quest_domain
from .grant_service import GrantService


def _completed(char_id: int) -> set[str]:
    """已完成任务 id 集合。"""
    return {str(row["quest_id"]) for row in QuestRepository.list_for(char_id)
            if str(row.get("status")) == "completed"}


def _snapshot(char_id: int) -> Dict[str, Any]:
    """绝对类目标同步所需的角色快照。"""
    char = CharacterRepository.require(char_id)
    items = {str(r["item_id"]): int(r["qty"]) for r in InventoryRepository.list_for(char_id)}
    kills: Dict[str, int] = {}
    for key, value in FlagRepository.all_for(char_id).items():
        if key.startswith("kill:"):
            try:
                kills[key.split(":", 1)[1]] = int(value)
            except (TypeError, ValueError):
                pass
    return {"level": int(char["level"]), "realm_index": int(char["realm_index"]),
            "items": items, "kills": kills}


def advance(char_id: int, event: Mapping[str, Any]) -> Dict[str, Any]:
    """把一次业务事件推给所有「进行中」的主线。

    Args:
        event: ``{type, ...}``，如 ``{"type":"kill","enemy_id":"x"}``。

    Returns:
        ``{changed: [quest_id...], ready: [quest_id...]}``
    """
    registry = get_registry()
    completed = _completed(char_id)
    changed: list[str] = []
    ready: list[str] = []
    for quest in registry.quest_chain():
        qid = str(quest["id"])
        if qid in completed:
            continue
        row = QuestRepository.get(char_id, qid)
        status = str((row or {}).get("status") or "locked")
        if status not in ("active", "ready"):
            # 前置满足但尚未落行的任务，视为 active
            if not quest_domain.prereq_met(quest, completed):
                continue
        progress = (row or {}).get("progress") or {}
        progress, did = quest_domain.advance(quest, progress, event)
        if did:
            changed.append(qid)
            QuestRepository.upsert(char_id, qid, status="active", step=0, progress=progress)
        if quest_domain.is_ready(quest, progress):
            ready.append(qid)
            QuestRepository.upsert(char_id, qid, status="ready", step=0, progress=progress)
    return {"changed": changed, "ready": ready}


def sync(char_id: int, quest_id: Optional[str] = None) -> Dict[str, Any]:
    """按当前角色状态同步绝对类目标（幂等）。"""
    registry = get_registry()
    completed = _completed(char_id)
    snap = _snapshot(char_id)
    targets = [registry.quest(quest_id)] if quest_id else list(registry.quest_chain())
    touched: list[str] = []
    for quest in targets:
        qid = str(quest["id"])
        if qid in completed:
            continue
        row = QuestRepository.get(char_id, qid)
        progress = (row or {}).get("progress") or {}
        progress, did = quest_domain.sync(quest, progress, snap)
        if did:
            status = "ready" if quest_domain.is_ready(quest, progress) else "active"
            QuestRepository.upsert(char_id, qid, status=status, step=0, progress=progress)
            touched.append(qid)
    return {"synced": touched}


def claim(char_id: int, quest_id: str) -> Dict[str, Any]:
    """领取主线奖励。

    Raises:
        GameError: 4030 任务不存在 / 4029 目标未达成 / 409 已领取。
    """
    registry = get_registry()
    try:
        quest = registry.quest(quest_id)
    except Exception:
        raise GameError(QUEST_UNKNOWN, f"任务不存在：{quest_id}")
    row = QuestRepository.get(char_id, quest_id)
    if row and str(row.get("status")) == "completed":
        raise GameError(QUEST_NOT_READY, "该任务奖励已领取", {"quest_id": quest_id})

    completed = _completed(char_id)
    if not quest_domain.prereq_met(quest, completed):
        raise GameError(QUEST_NOT_READY, "前置任务尚未完成", {"quest_id": quest_id})

    snap = _snapshot(char_id)
    progress = (row or {}).get("progress") or {}
    progress, _ = quest_domain.sync(quest, progress, snap)
    # 对话类目标：点击「领取」即视为完成对话（MVP 无独立对话面板）
    for index, obj in enumerate(quest.get("objectives", ())):
        if str(obj.get("type")) == "talk":
            key = str(index)
            progress[key] = max(int(progress.get(key, 0)), int(obj.get("count", 1)))
    if not quest_domain.is_ready(quest, progress):
        return_payload = {
            "quest_id": quest_id,
            "objectives": quest_domain.snapshot_of(quest, progress),
        }
        raise GameError(QUEST_NOT_READY, "任务目标尚未达成", return_payload)

    rewards = dict(quest.get("rewards") or {})
    # 交付类目标在领奖时扣道具
    consumed: Dict[str, int] = {}
    for obj in quest.get("objectives", ()):
        if str(obj.get("type")) == "deliver":
            item_id = str(obj.get("item_id"))
            qty = int(obj.get("count", 1))
            inventory = InventoryRepository.get(char_id, item_id)
            have = int(inventory["qty"]) if inventory else 0
            if have < qty:
                raise GameError(4016, f"缺少交付物 {item_id}",
                                {"item_id": item_id, "have": have, "need": qty})
            InventoryRepository.remove(char_id, item_id, qty)
            consumed[item_id] = qty

    result = GrantService.grant(char_id, rewards, source_type="quest", source_ref=f"quest:{quest_id}")
    QuestRepository.upsert(char_id, quest_id, status="completed", step=0, progress=progress)
    FlagRepository.set(char_id, f"quest_done:{quest_id}", "1")
    # 解锁后续任务
    for next_id in quest.get("unlock_next", ()) or ():
        if not QuestRepository.get(char_id, str(next_id)):
            QuestRepository.upsert(char_id, str(next_id), status="active", step=0, progress={})
    return {
        "quest_id": quest_id,
        "title": str(quest.get("title", "")),
        "rewards": rewards,
        "grant": result,
        "consumed": consumed,
        "next": list(quest.get("unlock_next", ()) or ()),
        "volume_end": bool(int(quest.get("order", 0)) == 19),
    }
