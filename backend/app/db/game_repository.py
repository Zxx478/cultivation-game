"""游戏主表数据访问层（character + 12 张子表 + 快照）。

与既有 ``repository.py`` 的关系：
- 本文件**只新增**，不改既有 Repository 的任何方法与签名；
- 复用同一连接（``get_conn()``）与同一事务工具（``transaction()``）；
- 唯一父表是 ``character``，其余 12 张子表全部 ``ON DELETE CASCADE``
  （``save_slot`` 例外：快照独立于角色存活）。

字段白名单：``CharacterRepository.update`` 只接受 ``CHARACTER_FIELDS`` 内的列，
表名/列名永不来自用户输入（安全基线 §9）。
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..core.errors import (
    NO_ACTIVE_CHARACTER,
    SAVE_SLOT_INVALID,
    VERSION_CONFLICT,
    GameError,
)
from .repository import _row_to_dict, get_conn, transaction

# character 表可写列（白名单；id/created_at 除外）
CHARACTER_FIELDS: Tuple[str, ...] = (
    "name", "gender", "personality", "physique", "talent",
    "level", "realm_index", "realm_stage", "cultivation", "exp_overflow",
    "hp", "mp", "age_days", "lifespan_max_days", "spirit_stone", "karma",
    "dao_heart", "str", "con", "spr", "sen", "bon", "wis", "agi", "luk",
    "region_id", "path_id", "sect_id", "direction_id", "foundation",
    "fail_streak", "realm_attempts", "bag_slots", "pending_event", "status",
    "rng_seed", "rng_counter", "last_settle_at", "version",
)

META_ACTIVE_CHAR_ID = "active_char_id"


def _now() -> int:
    """统一时间源（整数秒，仅用于 created_at / updated_at 审计）。"""
    return int(time.time())


def _dumps(value: Any) -> str:
    """JSON 序列化（中文不转义）。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    """JSON 反序列化（脏数据回退）。"""
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


# ============================================================
# character
# ============================================================


class CharacterRepository:
    """角色主表读写（含乐观锁与 rng 计数）。"""

    @staticmethod
    def get(char_id: int) -> Optional[Dict[str, Any]]:
        """按 id 取角色；不存在返回 None。"""
        row = get_conn().execute(
            "SELECT * FROM character WHERE id = ?", (int(char_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def require(char_id: int) -> Dict[str, Any]:
        """按 id 取角色；不存在抛 4003。"""
        row = CharacterRepository.get(char_id)
        if row is None:
            raise GameError(NO_ACTIVE_CHARACTER, f"角色不存在：{char_id}")
        return row

    @staticmethod
    def get_active() -> Optional[Dict[str, Any]]:
        """按 meta.active_char_id 取当前角色。"""
        char_id = MetaRepository.get(META_ACTIVE_CHAR_ID)
        if not char_id:
            return None
        try:
            return CharacterRepository.get(int(char_id))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def create(data: Mapping[str, Any]) -> int:
        """新建角色，返回 id。

        Args:
            data: 仅取 ``CHARACTER_FIELDS`` 内的键，缺省用表默认值。
        """
        payload = {k: v for k, v in data.items() if k in CHARACTER_FIELDS}
        payload.setdefault("created_at", _now())
        columns = list(payload.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = (f"INSERT INTO character ({', '.join(columns)}) "
               f"VALUES ({placeholders})")
        with transaction() as conn:
            cur = conn.execute(sql, tuple(payload[c] for c in columns))
            char_id = int(cur.lastrowid)
        MetaRepository.set(META_ACTIVE_CHAR_ID, str(char_id))
        return char_id

    @staticmethod
    def update(char_id: int, fields: Mapping[str, Any], *,
               expected_version: Optional[int] = None) -> int:
        """更新角色（乐观锁），返回新 version。

        Args:
            char_id: 角色 id。
            fields: 待更新列（白名单外的键被忽略）。
            expected_version: 若给定则做 CAS；不匹配抛 4004。

        Raises:
            GameError: 4003 角色不存在 / 4004 版本冲突。
        """
        payload = {k: v for k, v in fields.items() if k in CHARACTER_FIELDS}
        if expected_version is not None:
            payload["version"] = int(expected_version) + 1
        else:
            current = CharacterRepository.require(char_id)
            payload["version"] = int(current["version"]) + 1
        if not payload:
            return int(CharacterRepository.require(char_id)["version"])

        sets = ", ".join(f"{col} = ?" for col in payload)
        params: List[Any] = list(payload.values()) + [int(char_id)]
        sql = f"UPDATE character SET {sets} WHERE id = ?"
        if expected_version is not None:
            sql += " AND version = ?"
            params.append(int(expected_version))
        with transaction() as conn:
            cur = conn.execute(sql, tuple(params))
            if cur.rowcount == 0:
                raise GameError(VERSION_CONFLICT, "角色已在别处被修改，请重新载入",
                                {"char_id": char_id, "expected_version": expected_version})
        return int(payload["version"])

    @staticmethod
    def update_cas(char_id: int, fields: Mapping[str, Any],
                   expected_version: int) -> int:
        """``update`` 的 CAS 简写。"""
        return CharacterRepository.update(char_id, fields,
                                          expected_version=expected_version)

    @staticmethod
    def increment_rng(char_id: int, steps: int = 1) -> int:
        """原子递增 rng_counter，返回新值（与业务同事务时由调用方包事务）。"""
        conn = get_conn()
        conn.execute(
            "UPDATE character SET rng_counter = rng_counter + ? WHERE id = ?",
            (int(steps), int(char_id)),
        )
        row = conn.execute("SELECT rng_counter FROM character WHERE id = ?",
                           (int(char_id),)).fetchone()
        return int(row["rng_counter"]) if row else 0

    @staticmethod
    def delete(char_id: int) -> None:
        """删角色（子表级联由 FK + PRAGMA foreign_keys=ON 保证）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character WHERE id = ?", (int(char_id),))


# ============================================================
# meta
# ============================================================


class MetaRepository:
    """k/v 元信息。"""

    @staticmethod
    def get(key: str, default: Optional[str] = None) -> Optional[str]:
        """读 meta 值。"""
        row = get_conn().execute("SELECT value FROM meta WHERE key = ?",
                                 (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def set(key: str, value: str) -> None:
        """写 meta 值（upsert）。"""
        with transaction() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    @staticmethod
    def delete(key: str) -> None:
        """删 meta 键。"""
        with transaction() as conn:
            conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    @staticmethod
    def all() -> Dict[str, str]:
        """全部 meta（调试用）。"""
        rows = get_conn().execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ============================================================
# 角色子表
# ============================================================


class TechniqueRepository:
    """角色功法（character_technique）。"""

    EQUIP_SLOTS = ("heart", "atk1", "atk2", "atk3",
                   "def1", "def2", "def3", "sup1", "sup2", "sup3")

    @staticmethod
    def list_for(char_id: int) -> List[Dict[str, Any]]:
        """角色已学功法（按层数降序）。"""
        rows = get_conn().execute(
            "SELECT * FROM character_technique WHERE char_id = ? "
            "ORDER BY layer DESC, tech_id",
            (int(char_id),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    @staticmethod
    def get(char_id: int, tech_id: str) -> Optional[Dict[str, Any]]:
        """取单条。"""
        row = get_conn().execute(
            "SELECT * FROM character_technique WHERE char_id = ? AND tech_id = ?",
            (int(char_id), str(tech_id)),
        ).fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def upsert(char_id: int, tech_id: str, *, layer: int = 1, sense: int = 0,
               equipped_slot: str = "", learned_at: Optional[int] = None) -> None:
        """学习/更新功法（层数与感悟取较大值，避免覆盖回退）。"""
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO character_technique
                  (char_id, tech_id, layer, sense, equipped_slot, learned_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(char_id, tech_id) DO UPDATE SET
                  layer = MAX(character_technique.layer, excluded.layer),
                  sense = MAX(character_technique.sense, excluded.sense),
                  equipped_slot = CASE
                    WHEN excluded.equipped_slot != '' THEN excluded.equipped_slot
                    ELSE character_technique.equipped_slot END
                """,
                (int(char_id), str(tech_id), int(layer), int(sense),
                 str(equipped_slot), int(learned_at if learned_at is not None else _now())),
            )

    @staticmethod
    def equip(char_id: int, tech_id: str, slot: str) -> None:
        """装备到指定槽位（先清空该槽，保证一槽一件）。"""
        if slot not in TechniqueRepository.EQUIP_SLOTS:
            raise GameError(4017, f"非法功法槽位：{slot}", {"slot": slot})
        with transaction() as conn:
            conn.execute(
                "UPDATE character_technique SET equipped_slot = '' "
                "WHERE char_id = ? AND equipped_slot = ?", (int(char_id), slot))
            conn.execute(
                "UPDATE character_technique SET equipped_slot = ? "
                "WHERE char_id = ? AND tech_id = ?",
                (slot, int(char_id), str(tech_id)),
            )

    @staticmethod
    def unequip(char_id: int, slot: str) -> None:
        """卸下槽位。"""
        with transaction() as conn:
            conn.execute(
                "UPDATE character_technique SET equipped_slot = '' "
                "WHERE char_id = ? AND equipped_slot = ?", (int(char_id), slot))

    @staticmethod
    def clear(char_id: int) -> None:
        """清空角色功法（读档重灌用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_technique WHERE char_id = ?",
                         (int(char_id),))


class EquipmentRepository:
    """角色装备实例（character_equipment）。"""

    SLOTS = ("weapon", "armor", "helm", "accessory", "natal", "storage")

    @staticmethod
    def list_for(char_id: int) -> List[Dict[str, Any]]:
        """角色全部装备实例。"""
        rows = get_conn().execute(
            "SELECT * FROM character_equipment WHERE char_id = ? "
            "ORDER BY equipped DESC, instance_id",
            (int(char_id),),
        ).fetchall()
        out = []
        for row in rows:
            item = _row_to_dict(row)
            item["affixes"] = _loads(item.pop("affixes_json", "[]"), [])
            out.append(item)
        return out

    @staticmethod
    def create(char_id: int, template_id: str, *, slot: str, rarity: str = "common",
               enhance: int = 0, affixes: Optional[Sequence[Any]] = None,
               equipped: bool = False) -> int:
        """新建装备实例，返回 instance_id。"""
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO character_equipment
                  (char_id, template_id, slot, rarity, enhance, affixes_json,
                   equipped, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(char_id), str(template_id), str(slot), str(rarity),
                 int(enhance), _dumps(list(affixes or ())), int(bool(equipped)), _now()),
            )
            return int(cur.lastrowid)

    @staticmethod
    def equip(char_id: int, instance_id: int) -> str:
        """装备某实例（同槽只留一件），返回槽位名。"""
        row = get_conn().execute(
            "SELECT * FROM character_equipment WHERE instance_id = ? AND char_id = ?",
            (int(instance_id), int(char_id)),
        ).fetchone()
        if row is None:
            raise GameError(4018, f"装备不存在：{instance_id}")
        slot = str(row["slot"])
        with transaction() as conn:
            conn.execute(
                "UPDATE character_equipment SET equipped = 0 "
                "WHERE char_id = ? AND slot = ?", (int(char_id), slot))
            conn.execute(
                "UPDATE character_equipment SET equipped = 1 WHERE instance_id = ?",
                (int(instance_id),))
        return slot

    @staticmethod
    def unequip(char_id: int, slot: str) -> None:
        """卸下槽位。"""
        with transaction() as conn:
            conn.execute(
                "UPDATE character_equipment SET equipped = 0 "
                "WHERE char_id = ? AND slot = ?", (int(char_id), str(slot)))

    @staticmethod
    def clear(char_id: int) -> None:
        """清空角色装备（读档重灌用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_equipment WHERE char_id = ?",
                         (int(char_id),))


class InventoryRepository:
    """角色背包（character_inventory）。"""

    @staticmethod
    def list_for(char_id: int, *, item_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """背包条目（可按类型过滤）。"""
        sql = "SELECT * FROM character_inventory WHERE char_id = ?"
        params: List[Any] = [int(char_id)]
        if item_type:
            sql += " AND item_type = ?"
            params.append(str(item_type))
        sql += " ORDER BY item_type, item_id"
        rows = get_conn().execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]

    @staticmethod
    def get(char_id: int, item_id: str) -> Optional[Dict[str, Any]]:
        """取单条。"""
        row = get_conn().execute(
            "SELECT * FROM character_inventory WHERE char_id = ? AND item_id = ?",
            (int(char_id), str(item_id)),
        ).fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def add(char_id: int, item_id: str, item_type: str, qty: int) -> int:
        """入库（堆叠），返回入库后的数量。"""
        if qty <= 0:
            raise ValueError("qty must be > 0")
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO character_inventory (char_id, item_id, item_type, qty)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(char_id, item_id) DO UPDATE SET
                  qty = character_inventory.qty + excluded.qty
                """,
                (int(char_id), str(item_id), str(item_type), int(qty)),
            )
        row = InventoryRepository.get(char_id, item_id)
        return int(row["qty"]) if row else 0

    @staticmethod
    def remove(char_id: int, item_id: str, qty: int) -> int:
        """出库，返回剩余数量（不足抛 4016）。"""
        row = InventoryRepository.get(char_id, item_id)
        if row is None or int(row["qty"]) < qty:
            raise GameError(4016, f"物品不足：{item_id}", {"item_id": item_id})
        left = int(row["qty"]) - int(qty)
        with transaction() as conn:
            if left <= 0:
                conn.execute(
                    "DELETE FROM character_inventory WHERE char_id = ? AND item_id = ?",
                    (int(char_id), str(item_id)))
            else:
                conn.execute(
                    "UPDATE character_inventory SET qty = ? "
                    "WHERE char_id = ? AND item_id = ?",
                    (left, int(char_id), str(item_id)))
        return max(0, left)

    @staticmethod
    def distinct_slots(char_id: int) -> int:
        """已占用格数（一物品一格）。"""
        row = get_conn().execute(
            "SELECT COUNT(*) AS n FROM character_inventory WHERE char_id = ?",
            (int(char_id),)).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def clear(char_id: int) -> None:
        """清空背包（读档重灌用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_inventory WHERE char_id = ?",
                         (int(char_id),))


class QuestRepository:
    """角色主线进度（character_quest）。"""

    @staticmethod
    def list_for(char_id: int) -> List[Dict[str, Any]]:
        """全部任务进度（带解析后的 progress）。"""
        rows = get_conn().execute(
            "SELECT * FROM character_quest WHERE char_id = ? ORDER BY quest_id",
            (int(char_id),),
        ).fetchall()
        out = []
        for row in rows:
            item = _row_to_dict(row)
            item["progress"] = _loads(item.pop("progress_json", "{}"), {})
            out.append(item)
        return out

    @staticmethod
    def get(char_id: int, quest_id: str) -> Optional[Dict[str, Any]]:
        """取单条任务。"""
        row = get_conn().execute(
            "SELECT * FROM character_quest WHERE char_id = ? AND quest_id = ?",
            (int(char_id), str(quest_id)),
        ).fetchone()
        if row is None:
            return None
        item = _row_to_dict(row)
        item["progress"] = _loads(item.pop("progress_json", "{}"), {})
        return item

    @staticmethod
    def upsert(char_id: int, quest_id: str, *, status: str, step: int = 0,
               progress: Optional[Mapping[str, Any]] = None) -> None:
        """写入/更新任务进度。"""
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO character_quest
                  (char_id, quest_id, status, step, progress_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(char_id, quest_id) DO UPDATE SET
                  status = excluded.status,
                  step = excluded.step,
                  progress_json = excluded.progress_json,
                  updated_at = excluded.updated_at
                """,
                (int(char_id), str(quest_id), str(status), int(step),
                 _dumps(dict(progress or {})), _now()),
            )

    @staticmethod
    def clear(char_id: int) -> None:
        """清空任务（读档重灌用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_quest WHERE char_id = ?",
                         (int(char_id),))


class FlagRepository:
    """角色标记（character_flag）。"""

    @staticmethod
    def get(char_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
        """读标记。"""
        row = get_conn().execute(
            "SELECT value FROM character_flag WHERE char_id = ? AND key = ?",
            (int(char_id), str(key)),
        ).fetchone()
        return row["value"] if row else default

    @staticmethod
    def set(char_id: int, key: str, value: Any = "1") -> None:
        """写标记（upsert）。"""
        with transaction() as conn:
            conn.execute(
                "INSERT INTO character_flag (char_id, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(char_id, key) DO UPDATE SET value = excluded.value",
                (int(char_id), str(key), str(value)),
            )

    @staticmethod
    def all_for(char_id: int) -> Dict[str, str]:
        """全部标记。"""
        rows = get_conn().execute(
            "SELECT key, value FROM character_flag WHERE char_id = ?",
            (int(char_id),)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    @staticmethod
    def clear(char_id: int) -> None:
        """清空标记。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_flag WHERE char_id = ?",
                         (int(char_id),))


class RelationRepository:
    """角色与 NPC 关系（character_relation）。"""

    @staticmethod
    def list_for(char_id: int) -> List[Dict[str, Any]]:
        """全部关系。"""
        rows = get_conn().execute(
            "SELECT * FROM character_relation WHERE char_id = ? ORDER BY npc_id",
            (int(char_id),)).fetchall()
        return [_row_to_dict(r) for r in rows]

    @staticmethod
    def add_favor(char_id: int, npc_id: str, favor: int = 0, karma: int = 0) -> Dict[str, Any]:
        """累加好感/因果（夹 −100..100）。"""
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO character_relation (char_id, npc_id, favor, karma)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(char_id, npc_id) DO UPDATE SET
                  favor = MAX(-100, MIN(100, character_relation.favor + excluded.favor)),
                  karma = character_relation.karma + excluded.karma
                """,
                (int(char_id), str(npc_id), int(favor), int(karma)),
            )
        row = get_conn().execute(
            "SELECT * FROM character_relation WHERE char_id = ? AND npc_id = ?",
            (int(char_id), str(npc_id))).fetchone()
        return _row_to_dict(row) if row else {}

    @staticmethod
    def clear(char_id: int) -> None:
        """清空关系。"""
        with transaction() as conn:
            conn.execute("DELETE FROM character_relation WHERE char_id = ?",
                         (int(char_id),))


class CombatSessionRepository:
    """战斗会话（一角色一会话）。"""

    @staticmethod
    def get(char_id: int) -> Optional[Dict[str, Any]]:
        """取会话（不存在返回 None）。"""
        row = get_conn().execute(
            "SELECT * FROM combat_session WHERE char_id = ?", (int(char_id),)).fetchone()
        if row is None:
            return None
        item = _row_to_dict(row)
        item["session"] = _loads(item.pop("session_json", "{}"), {})
        return item

    @staticmethod
    def save(char_id: int, session: Mapping[str, Any], *, status: str,
             turn: int) -> None:
        """写入/覆盖会话。"""
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO combat_session
                  (char_id, session_json, status, turn, updated_at, version)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(char_id) DO UPDATE SET
                  session_json = excluded.session_json,
                  status = excluded.status,
                  turn = excluded.turn,
                  updated_at = excluded.updated_at,
                  version = combat_session.version + 1
                """,
                (int(char_id), _dumps(dict(session)), str(status), int(turn), _now()),
            )

    @staticmethod
    def delete(char_id: int) -> None:
        """删除会话（战斗结束/读档时调用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM combat_session WHERE char_id = ?",
                         (int(char_id),))


class LogRepository:
    """事件日志与里程碑日志（限长裁剪）。"""

    EVENT_LIMIT = 500
    MILESTONE_LIMIT = 200

    @staticmethod
    def add_event(char_id: int, kind: str, payload: Mapping[str, Any]) -> None:
        """写事件日志并在超长时裁剪。"""
        with transaction() as conn:
            conn.execute(
                "INSERT INTO event_log (char_id, kind, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (int(char_id), str(kind), _dumps(dict(payload)), _now()),
            )
            LogRepository._trim(conn, "event_log", char_id, LogRepository.EVENT_LIMIT)

    @staticmethod
    def add_milestone(char_id: int, title: str, detail: str = "") -> None:
        """写里程碑日志并在超长时裁剪。"""
        with transaction() as conn:
            conn.execute(
                "INSERT INTO milestone_log (char_id, title, detail, created_at) "
                "VALUES (?, ?, ?, ?)",
                (int(char_id), str(title), str(detail), _now()),
            )
            LogRepository._trim(conn, "milestone_log", char_id,
                                LogRepository.MILESTONE_LIMIT)

    @staticmethod
    def _trim(conn: Any, table: str, char_id: int, limit: int) -> None:
        """按 log_id 保留最近 ``limit`` 条（表名来自内部常量，非用户输入）。"""
        conn.execute(
            f"DELETE FROM {table} WHERE char_id = ? AND log_id NOT IN "
            f"(SELECT log_id FROM {table} WHERE char_id = ? "
            f"ORDER BY log_id DESC LIMIT ?)",
            (int(char_id), int(char_id), int(limit)),
        )

    @staticmethod
    def events(char_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """最近事件日志（新→旧）。"""
        rows = get_conn().execute(
            "SELECT * FROM event_log WHERE char_id = ? ORDER BY log_id DESC LIMIT ?",
            (int(char_id), int(limit))).fetchall()
        out = []
        for row in rows:
            item = _row_to_dict(row)
            item["payload"] = _loads(item.pop("payload_json", "{}"), {})
            out.append(item)
        return out

    @staticmethod
    def milestones(char_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """最近里程碑（新→旧）。"""
        rows = get_conn().execute(
            "SELECT * FROM milestone_log WHERE char_id = ? ORDER BY log_id DESC LIMIT ?",
            (int(char_id), int(limit))).fetchall()
        return [_row_to_dict(r) for r in rows]

    @staticmethod
    def clear(char_id: int) -> None:
        """清空日志。"""
        with transaction() as conn:
            conn.execute("DELETE FROM event_log WHERE char_id = ?", (int(char_id),))
            conn.execute("DELETE FROM milestone_log WHERE char_id = ?", (int(char_id),))


class GrantLogRepository:
    """发奖幂等日志（reward_grant_log，UNIQUE(char_id,source_type,source_ref)）。"""

    @staticmethod
    def exists(char_id: int, source_type: str, source_ref: str) -> bool:
        """该来源是否已发过奖。"""
        row = get_conn().execute(
            "SELECT 1 FROM reward_grant_log "
            "WHERE char_id = ? AND source_type = ? AND source_ref = ? LIMIT 1",
            (int(char_id), str(source_type), str(source_ref)),
        ).fetchone()
        return row is not None

    @staticmethod
    def record(char_id: int, source_type: str, source_ref: str,
               payload: Mapping[str, Any]) -> bool:
        """写入发奖记录；已存在返回 False（调用方据此跳过重复发放）。"""
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO reward_grant_log "
                "(char_id, source_type, source_ref, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (int(char_id), str(source_type), str(source_ref),
                 _dumps(dict(payload)), _now()),
            )
            return cur.rowcount > 0

    @staticmethod
    def all_for(char_id: int) -> List[Dict[str, Any]]:
        """角色全部发奖记录（快照用）。"""
        rows = get_conn().execute(
            "SELECT * FROM reward_grant_log WHERE char_id = ? ORDER BY grant_id",
            (int(char_id),)).fetchall()
        out = []
        for row in rows:
            item = _row_to_dict(row)
            item["payload"] = _loads(item.pop("payload_json", "{}"), {})
            out.append(item)
        return out

    @staticmethod
    def clear(char_id: int) -> None:
        """清空发奖记录（读档重灌用）。"""
        with transaction() as conn:
            conn.execute("DELETE FROM reward_grant_log WHERE char_id = ?",
                         (int(char_id),))


class SectQuestRecordRepository:
    """宗门任务贡献记录（既有表，只读聚合）。"""

    @staticmethod
    def sum_contribution(char_id: int) -> int:
        """角色累计宗门任务贡献（方向树点亮判定用）。"""
        row = get_conn().execute(
            "SELECT COALESCE(SUM(contribution_gained), 0) AS total "
            "FROM sect_quest_record WHERE character_id = ?",
            (int(char_id),),
        ).fetchone()
        return int(row["total"] or 0) if row else 0


class SectActiveQuestRepository:
    """宗门进行中任务（既有表，只读）。"""

    @staticmethod
    def active_for(char_id: int) -> Optional[Dict[str, Any]]:
        """取最近一条 accepted 状态的宗门任务。"""
        row = get_conn().execute(
            "SELECT * FROM sect_active_quest WHERE character_id = ? AND status = 'accepted' "
            "ORDER BY quest_id DESC LIMIT 1",
            (int(char_id),),
        ).fetchone()
        return _row_to_dict(row) if row else None


# ============================================================
# 存档槽
# ============================================================


class SaveSlotRepository:
    """存档槽（0=自动，1–3 手动；不设 FK）。"""

    AUTO_SLOT = 0
    MANUAL_SLOTS = (1, 2, 3)

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        """全部槽位（含空槽占位）。"""
        rows = get_conn().execute("SELECT * FROM save_slot ORDER BY slot_id").fetchall()
        stored = {int(r["slot_id"]): _row_to_dict(r) for r in rows}
        out: List[Dict[str, Any]] = []
        for slot_id in (SaveSlotRepository.AUTO_SLOT,) + SaveSlotRepository.MANUAL_SLOTS:
            row = stored.get(slot_id)
            if row is None:
                out.append({
                    "slot_id": slot_id, "char_id": None, "label": "",
                    "exists": False, "created_at": 0, "content_version": "",
                    "content_hash": "", "checksum": "",
                })
                continue
            row.pop("snapshot_json", None)
            row["exists"] = True
            out.append(row)
        return out

    @staticmethod
    def get(slot_id: int) -> Optional[Dict[str, Any]]:
        """取槽位（含解析后的 snapshot）。"""
        row = get_conn().execute(
            "SELECT * FROM save_slot WHERE slot_id = ?", (int(slot_id),)).fetchone()
        if row is None:
            return None
        item = _row_to_dict(row)
        item["snapshot"] = _loads(item.pop("snapshot_json", "{}"), {})
        return item

    @staticmethod
    def upsert(slot_id: int, *, char_id: int, snapshot: Mapping[str, Any],
               content_version: str, content_hash: str, checksum: str,
               label: str = "") -> None:
        """写入/覆盖槽位。

        Raises:
            GameError: 4019 槽位非法（自动槽不可被手动覆盖的语义由 service 保证）。
        """
        if int(slot_id) not in (SaveSlotRepository.AUTO_SLOT,) + SaveSlotRepository.MANUAL_SLOTS:
            raise GameError(SAVE_SLOT_INVALID, f"非法存档槽：{slot_id}")
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO save_slot
                  (slot_id, char_id, label, snapshot_json, content_version,
                   content_hash, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot_id) DO UPDATE SET
                  char_id = excluded.char_id,
                  label = excluded.label,
                  snapshot_json = excluded.snapshot_json,
                  content_version = excluded.content_version,
                  content_hash = excluded.content_hash,
                  checksum = excluded.checksum,
                  created_at = excluded.created_at
                """,
                (int(slot_id), int(char_id), str(label), _dumps(dict(snapshot)),
                 str(content_version), str(content_hash), str(checksum), _now()),
            )

    @staticmethod
    def delete(slot_id: int) -> None:
        """删槽位（不动 character）。"""
        if int(slot_id) == SaveSlotRepository.AUTO_SLOT:
            raise GameError(SAVE_SLOT_INVALID, "自动存档槽不可删除",
                            {"slot_id": slot_id})
        with transaction() as conn:
            conn.execute("DELETE FROM save_slot WHERE slot_id = ?", (int(slot_id),))


# ============================================================
# 快照 / 恢复
# ============================================================

SNAPSHOT_SCHEMA_VERSION = 1


def _list_sect_reputations(char_id: int) -> List[Dict[str, Any]]:
    """散修对 6 宗门的独立好感/贡献（快照用；既有 Repository 只提供 get/upsert）。"""
    rows = get_conn().execute(
        "SELECT * FROM sect_reputation WHERE character_id = ? ORDER BY sect_id",
        (int(char_id),)).fetchall()
    return [_row_to_dict(r) for r in rows]


def _list_forbidden_progress(char_id: int) -> List[Dict[str, Any]]:
    """禁地进度（快照用；既有 Repository 只提供 get/upsert）。"""
    rows = get_conn().execute(
        "SELECT * FROM forbidden_land_progress WHERE character_id = ? ORDER BY land_id",
        (int(char_id),)).fetchall()
    return [_row_to_dict(r) for r in rows]


def build_snapshot(char_id: int) -> Dict[str, Any]:
    """构造完整快照（character + 6 张角色子表 + 4 张宗门表）。

    Args:
        char_id: 角色 id。

    Returns:
        可 JSON 序列化的字典；角色不存在抛 4003。
    """
    from .repository import SansxiuWalletRepository, SectMemberRepository

    char = CharacterRepository.require(char_id)
    community: Dict[str, Any] = {}
    member = SectMemberRepository.get(char_id)
    if member:
        community["sect_member"] = member
    wallet = SansxiuWalletRepository.get(char_id)
    if wallet and wallet.get("character_id"):
        community["sansxiu_wallet"] = wallet
    reputations = _list_sect_reputations(char_id)
    if reputations:
        community["sect_reputation"] = reputations
    progress = _list_forbidden_progress(char_id)
    if progress:
        community["forbidden_land_progress"] = progress

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "character": char,
        "character_technique": TechniqueRepository.list_for(char_id),
        "character_equipment": EquipmentRepository.list_for(char_id),
        "character_inventory": InventoryRepository.list_for(char_id),
        "character_quest": QuestRepository.list_for(char_id),
        "character_flag": FlagRepository.all_for(char_id),
        "character_relation": RelationRepository.list_for(char_id),
        "community": community,
    }


def restore_snapshot(snapshot: Mapping[str, Any], *,
                     char_id: Optional[int] = None,
                     expected_version: Optional[int] = None) -> int:
    """按设计 §3.3 顺序恢复快照，返回角色 id。

    顺序（**不可调换**）：
    ① ``character`` UPDATE（校验 version）→ ② 清空并重灌 6 张角色子表 →
    ③ upsert 宗门相关表 → ④ 删除 combat_session → ⑤ version 由步骤 ① 递增一次。

    Args:
        snapshot: ``build_snapshot`` 的结构。
        char_id: 目标角色；None 表示按快照新建角色。
        expected_version: 读档时的乐观锁版本（None 表示不校验）。

    Raises:
        GameError: 4003/4004/4020。
    """
    from .repository import (
        ForbiddenLandProgressRepository,
        SansxiuWalletRepository,
        SectMemberRepository,
        SectReputationRepository,
    )

    char = dict(snapshot.get("character") or {})
    if not char:
        raise GameError(4020, "快照缺少 character 段")

    if char_id is None:
        payload = {k: v for k, v in char.items() if k in CHARACTER_FIELDS}
        payload["rng_counter"] = int(char.get("rng_counter", 0))
        char_id = CharacterRepository.create(payload)
    else:
        fields = {k: v for k, v in char.items()
                  if k in CHARACTER_FIELDS and k != "version"}
        fields["rng_counter"] = int(char.get("rng_counter", 0))
        CharacterRepository.update(char_id, fields, expected_version=expected_version)

    TechniqueRepository.clear(char_id)
    for row in snapshot.get("character_technique", ()):
        TechniqueRepository.upsert(
            char_id, row["tech_id"], layer=int(row.get("layer", 1)),
            sense=int(row.get("sense", 0)),
            equipped_slot=str(row.get("equipped_slot", "")),
            learned_at=int(row.get("learned_at", 0) or _now()))

    EquipmentRepository.clear(char_id)
    for row in snapshot.get("character_equipment", ()):
        EquipmentRepository.create(
            char_id, row["template_id"], slot=str(row.get("slot", "weapon")),
            rarity=str(row.get("rarity", "common")), enhance=int(row.get("enhance", 0)),
            affixes=row.get("affixes", ()), equipped=bool(row.get("equipped", 0)))

    InventoryRepository.clear(char_id)
    for row in snapshot.get("character_inventory", ()):
        if int(row.get("qty", 0)) > 0:
            InventoryRepository.add(char_id, row["item_id"],
                                    str(row.get("item_type", "material")),
                                    int(row["qty"]))

    QuestRepository.clear(char_id)
    for row in snapshot.get("character_quest", ()):
        QuestRepository.upsert(char_id, row["quest_id"],
                               status=str(row.get("status", "locked")),
                               step=int(row.get("step", 0)),
                               progress=row.get("progress", {}))

    FlagRepository.clear(char_id)
    for key, value in dict(snapshot.get("character_flag", {})).items():
        FlagRepository.set(char_id, key, value)

    RelationRepository.clear(char_id)
    for row in snapshot.get("character_relation", ()):
        RelationRepository.add_favor(char_id, row["npc_id"],
                                     int(row.get("favor", 0)),
                                     int(row.get("karma", 0)))

    community = dict(snapshot.get("community", {}))
    if community.get("sect_member"):
        SectMemberRepository.upsert(dict(community["sect_member"]))
    if community.get("sansxiu_wallet"):
        SansxiuWalletRepository.upsert(dict(community["sansxiu_wallet"]))
    for row in community.get("sect_reputation", ()):
        SectReputationRepository.upsert(dict(row))
    for row in community.get("forbidden_land_progress", ()):
        ForbiddenLandProgressRepository.upsert(dict(row))

    CombatSessionRepository.delete(char_id)
    return int(char_id)
