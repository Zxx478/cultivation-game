"""主线任务：目标进度推进与完成判定。

状态机（系统设计 §6）::

    locked →(prereq 全 completed) active →(目标满足) ready →(claim) completed

支持 13 种目标类型：
``talk / cultivate / kill / collect / deliver / reach_level / breakthrough /
join_sect / choose_direction / complete_sect_quest / sansxiu_encounter /
enter_forbidden / event_choice``。

进度存储结构（``character_quest.progress_json``）::

    {"0": 1, "1": 3}      # 键 = objectives 下标，值 = 已完成计数（封顶）

两类推进方式：
- **增量类**（talk/kill/collect/deliver/ cultivate/complete_sect_quest/
  sansxiu_encounter/enter_forbidden/event_choice/join_sect/choose_direction）：
  业务动作发生时调用 ``advance(quest, progress, event)``；
- **绝对类**（reach_level/breakthrough/collect）：读当前状态调用
  ``sync(quest, progress, snapshot)``，幂等且可越级补齐。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

STATUS_LOCKED = "locked"
STATUS_ACTIVE = "active"
STATUS_READY = "ready"
STATUS_COMPLETED = "completed"

RULE_ALL = "all"
RULE_ANY_GROUP = "any_group"

# 增量类目标（event 携带 type + 匹配键）
INCREMENTAL_TYPES = {
    "talk", "kill", "deliver", "cultivate", "complete_sect_quest",
    "sansxiu_encounter", "enter_forbidden", "event_choice", "join_sect",
    "choose_direction", "collect",
}
# 绝对类目标（由当前状态推导）
ABSOLUTE_TYPES = {"reach_level", "breakthrough"}

# 各类型用于匹配的键（缺省表示「无视细节，只比 type」）
MATCH_KEYS: Dict[str, str] = {
    "talk": "npc_id",
    "kill": "enemy_id",
    "collect": "item_id",
    "deliver": "item_id",
    "enter_forbidden": "land_id",
    "event_choice": "event_id",
}


@dataclass(frozen=True)
class ObjectiveProgress:
    """单个目标的进度快照。"""

    index: int
    type: str
    group: str
    current: int
    required: int

    @property
    def done(self) -> bool:
        """是否已达成。"""
        return self.current >= self.required


def _required(obj: Mapping[str, Any]) -> int:
    """目标所需次数（默认 1）。"""
    return max(1, int(obj.get("count", 1)))


def objective_required(obj: Mapping[str, Any]) -> int:
    """对外暴露：某目标所需次数。"""
    return _required(obj)


def initial_progress(quest: Mapping[str, Any]) -> Dict[str, int]:
    """新任务的初始进度（全 0）。"""
    return {str(i): 0 for i, _ in enumerate(quest["objectives"])}


def normalize_progress(quest: Mapping[str, Any],
                       progress: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    """补齐缺失键、裁掉多余键、夹到 required（防脏存档）。"""
    out = initial_progress(quest)
    for idx_str, value in (progress or {}).items():
        key = str(idx_str)
        if key not in out:
            continue
        index = int(key)
        cap = _required(quest["objectives"][index])
        out[key] = max(0, min(cap, int(value)))
    return out


def snapshot_of(quest: Mapping[str, Any],
                progress: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """把进度展开成 ``[{index, type, group, current, required, done}]``。"""
    norm = normalize_progress(quest, progress)
    rows: List[Dict[str, Any]] = []
    for index, obj in enumerate(quest["objectives"]):
        required = _required(obj)
        current = norm[str(index)]
        rows.append({
            "index": index,
            "type": str(obj["type"]),
            "group": str(obj.get("group", "main")),
            "current": current,
            "required": required,
            "done": current >= required,
        })
    return rows


def _matches(obj: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """判断一次业务事件是否命中某目标。"""
    if str(obj["type"]) != str(event.get("type")):
        return False
    key = MATCH_KEYS.get(str(obj["type"]))
    if key is None:
        # cultivate / complete_sect_quest / sansxiu_encounter 等只比类型
        if str(obj["type"]) == "join_sect" and event.get("sect_id") is not None:
            accept = obj.get("accept_sect_ids")
            return accept is None or str(event["sect_id"]) in {str(x) for x in accept}
        if str(obj["type"]) == "choose_direction" and event.get("direction") is not None:
            allowed = obj.get("directions")
            return allowed is None or str(event["direction"]) in {str(x) for x in allowed}
        return True
    if event.get(key) is None:
        return False
    return str(obj.get(key)) == str(event.get(key))


def advance(quest: Mapping[str, Any], progress: Optional[Mapping[str, Any]],
            event: Mapping[str, Any]) -> Tuple[Dict[str, int], bool]:
    """按一次业务事件推进进度。

    Args:
        quest: 任务配置。
        progress: 当前进度（可为 None）。
        event: ``{type, <匹配键>, count?}``，如 ``{"type":"kill","enemy_id":"x"}``。

    Returns:
        ``(new_progress, changed)``；``changed`` 表示本次是否真的推进了。
    """
    norm = normalize_progress(quest, progress)
    delta = max(1, int(event.get("count", 1)))
    changed = False
    for index, obj in enumerate(quest["objectives"]):
        if not _matches(obj, event):
            continue
        key = str(index)
        cap = _required(obj)
        new_value = min(cap, norm[key] + delta)
        if new_value != norm[key]:
            norm[key] = new_value
            changed = True
    return norm, changed


def sync(quest: Mapping[str, Any], progress: Optional[Mapping[str, Any]],
         snapshot: Mapping[str, Any]) -> Tuple[Dict[str, int], bool]:
    """按当前角色状态同步「绝对类」目标（幂等，可越级补齐）。

    Args:
        quest: 任务配置。
        progress: 当前进度。
        snapshot: ``{level, realm_index, items: {item_id: qty}, kills: {...}}``。

    Returns:
        ``(new_progress, changed)``。
    """
    norm = normalize_progress(quest, progress)
    changed = False
    level = int(snapshot.get("level", 0))
    realm_index = int(snapshot.get("realm_index", 0))
    items = {str(k): int(v) for k, v in dict(snapshot.get("items", {})).items()}
    kills = {str(k): int(v) for k, v in dict(snapshot.get("kills", {})).items()}

    for index, obj in enumerate(quest["objectives"]):
        key = str(index)
        otype = str(obj["type"])
        cap = _required(obj)
        value = norm[key]
        if otype == "reach_level":
            need_level = int(obj.get("level", 1))
            value = cap if level >= need_level else 0
        elif otype == "breakthrough":
            need_realm = int(obj.get("realm_index", 1))
            value = cap if realm_index >= need_realm else 0
        elif otype == "collect":
            have = items.get(str(obj.get("item_id")), 0)
            value = min(cap, have)
        elif otype == "kill" and kills:
            have = kills.get(str(obj.get("enemy_id")), 0)
            if have:
                value = min(cap, max(value, have))
        if value != norm[key]:
            norm[key] = value
            changed = True
    return norm, changed


def is_ready(quest: Mapping[str, Any],
             progress: Optional[Mapping[str, Any]]) -> bool:
    """目标是否全部满足（按 ``objective_rule``）。

    - ``all``：所有目标达成；
    - ``any_group``：任一分组内目标全部达成（散修/宗门二选一）。
    """
    rows = snapshot_of(quest, progress)
    if not rows:
        return True
    rule = str(quest.get("objective_rule", RULE_ALL))
    if rule == RULE_ANY_GROUP:
        groups: Dict[str, List[Mapping[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row["group"]), []).append(row)
        return any(all(bool(r["done"]) for r in items) for items in groups.values())
    return all(bool(r["done"]) for r in rows)


def prereq_met(quest: Mapping[str, Any],
               completed: Sequence[str] | set[str]) -> bool:
    """前置任务是否全部完成。"""
    done = {str(x) for x in completed}
    return all(str(p) in done for p in quest.get("prereq", ()))


def status_of(quest: Mapping[str, Any], *, completed: Sequence[str] | set[str],
              progress: Optional[Mapping[str, Any]]) -> str:
    """计算任务状态（locked / active / ready / completed）。"""
    if str(quest["id"]) in {str(x) for x in completed}:
        return STATUS_COMPLETED
    if not prereq_met(quest, completed):
        return STATUS_LOCKED
    return STATUS_READY if is_ready(quest, progress) else STATUS_ACTIVE


def newly_unlocked(quest: Mapping[str, Any],
                   completed: Sequence[str] | set[str]) -> Tuple[str, ...]:
    """某任务完成后应解锁的后继（其 prereq 已全满足）。"""
    done = {str(x) for x in completed} | {str(quest["id"])}
    out: List[str] = []
    for next_id in quest.get("unlock_next", ()):
        out.append(str(next_id))
    return tuple(out)


def auto_active_quests(quests: Sequence[Mapping[str, Any]],
                       completed: Sequence[str] | set[str]) -> Tuple[str, ...]:
    """扫描全部任务，返回当前应处于 ``active``（prereq 满足且未完成）的 id。"""
    done = {str(x) for x in completed}
    return tuple(str(q["id"]) for q in quests
                 if str(q["id"]) not in done and prereq_met(q, done))


def claimable(quest: Mapping[str, Any], progress: Optional[Mapping[str, Any]],
              completed: Sequence[str] | set[str]) -> bool:
    """是否可领奖（= 状态为 ready）。"""
    return status_of(quest, completed=completed, progress=progress) == STATUS_READY
