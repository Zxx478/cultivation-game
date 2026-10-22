"""SQLite 数据访问层。

为简化示例，使用 stdlib `sqlite3` 同步访问，连接串带 `check_same_thread=False`，
适合 FastAPI 单进程 ThreadPoolExecutor 调度；生产可换 aiosqlite / SQLAlchemy。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional


_SCHEMA_PATH = Path(__file__).resolve().parent / "sect_contribution.sql"
# 游戏主表 DDL（完整 character + 12 张子表），必须先于宗门 DDL 执行：
# 宗门 DDL 内的 `CREATE TABLE IF NOT EXISTS character` 会因已存在而变成 no-op。
_GAME_SCHEMA_PATH = Path(__file__).resolve().parent / "game.sql"

# 进程级连接（仅当当前线程没有注入连接时兜底）与写锁。
# 顺序铁律：**先看 thread_local，再看 _global_conn**——`tests/conftest.py`
# 靠 `_thread_local.conn = conn` 注入内存库，若反过来会让 28 个既有测试全红。
_global_conn: Optional[sqlite3.Connection] = None
_lock = threading.RLock()

# character 表「应然」列定义（旧库补列用；ALTER TABLE 只增不删）
_CHARACTER_COLUMNS: "dict[str, str]" = {
    "name": "TEXT NOT NULL DEFAULT ''",
    "gender": "TEXT NOT NULL DEFAULT 'unknown'",
    "personality": "TEXT NOT NULL DEFAULT 'zhongyong'",
    "physique": "TEXT NOT NULL DEFAULT 'fan_ti'",
    "talent": "INTEGER NOT NULL DEFAULT 5",
    "level": "INTEGER NOT NULL DEFAULT 1",
    "realm_index": "INTEGER NOT NULL DEFAULT 1",
    "realm_stage": "INTEGER NOT NULL DEFAULT 1",
    "cultivation": "INTEGER NOT NULL DEFAULT 0",
    "exp_overflow": "INTEGER NOT NULL DEFAULT 0",
    "hp": "INTEGER NOT NULL DEFAULT 0",
    "mp": "INTEGER NOT NULL DEFAULT 0",
    "age_days": "INTEGER NOT NULL DEFAULT 1",
    "lifespan_max_days": "INTEGER NOT NULL DEFAULT 36000",
    "spirit_stone": "INTEGER NOT NULL DEFAULT 0",
    "karma": "INTEGER NOT NULL DEFAULT 0",
    "dao_heart": "INTEGER NOT NULL DEFAULT 10",
    "str": "INTEGER NOT NULL DEFAULT 10",
    "con": "INTEGER NOT NULL DEFAULT 10",
    "spr": "INTEGER NOT NULL DEFAULT 10",
    "sen": "INTEGER NOT NULL DEFAULT 10",
    "bon": "INTEGER NOT NULL DEFAULT 10",
    "wis": "INTEGER NOT NULL DEFAULT 10",
    "agi": "INTEGER NOT NULL DEFAULT 10",
    "luk": "INTEGER NOT NULL DEFAULT 5",
    "region_id": "TEXT NOT NULL DEFAULT 'duanhuai_village'",
    "path_id": "TEXT NOT NULL DEFAULT ''",
    "sect_id": "TEXT",
    "direction_id": "TEXT NOT NULL DEFAULT ''",
    "foundation": "TEXT NOT NULL DEFAULT ''",
    "fail_streak": "INTEGER NOT NULL DEFAULT 0",
    "realm_attempts": "INTEGER NOT NULL DEFAULT 0",
    "bag_slots": "INTEGER NOT NULL DEFAULT 60",
    "pending_event": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'normal'",
    "rng_seed": "TEXT NOT NULL DEFAULT ''",
    "rng_counter": "INTEGER NOT NULL DEFAULT 0",
    "last_settle_at": "INTEGER NOT NULL DEFAULT 0",
    "version": "INTEGER NOT NULL DEFAULT 1",
    "created_at": "INTEGER NOT NULL DEFAULT 0",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """按「游戏主表 → 宗门表」顺序建表（两个脚本都是 IF NOT EXISTS，幂等）。"""
    for path in (_GAME_SCHEMA_PATH, _SCHEMA_PATH):
        if not path.exists():  # 兼容只跑宗门系统的旧部署
            continue
        with open(path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())


def _ensure_character_columns(conn: sqlite3.Connection) -> None:
    """为旧库补 `character` 缺失列（只 ADD COLUMN，绝不 DROP/RENAME）。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(character)").fetchall()}
    if not existing:
        return
    for column, ddl in _CHARACTER_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE character ADD COLUMN {column} {ddl}")
    conn.commit()


def init_db(db_path: str = ":memory:", run_schema: bool = True) -> sqlite3.Connection:
    """初始化数据库连接。默认内存库，便于测试。

    Args:
        db_path: SQLite 路径；``:memory:`` 表示内存库（测试）。
        run_schema: 是否执行 DDL（game.sql → sect_contribution.sql）。

    Returns:
        已配置 ``row_factory`` 与 ``foreign_keys`` 的连接。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path != ":memory:":
        # WAL 只对文件库有意义；内存库设置会报错
        conn.execute("PRAGMA journal_mode=WAL")
    if run_schema:
        ensure_schema(conn)
        _ensure_character_columns(conn)
    return conn


# Repository 使用 ThreadLocal 保证每个线程独立事务，避免并发污染
_thread_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """返回当前连接：**线程注入优先**，其次进程级连接，否则报错。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        return conn
    if _global_conn is not None:
        return _global_conn
    raise RuntimeError("Repository 未初始化：请先调用 init_global_conn")


def init_global_conn(db_path: str = ":memory:") -> sqlite3.Connection:
    """进程级初始化，被 FastAPI startup 调用。

    同时设置 ``_global_conn``（工作线程用）与 ``_thread_local.conn``（主线程用），
    使「请求线程」和「测试主线程」拿到的是同一个库。
    """
    global _global_conn
    conn = init_db(db_path)
    _global_conn = conn
    _thread_local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """事务上下文：同一时刻只允许一个写事务（进程内串行化）。

    Python sqlite3 模块的默认行为：DML (INSERT/UPDATE/DELETE) 会隐式开启事务，
    我们这里不显式 BEGIN，仅在外层做 commit/rollback——避免"cannot start a
    transaction within a transaction"错误。
    """
    conn = get_conn()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------------- 基础 Row → dict ----------------


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------- Repository ----------------


class SectMemberRepository:

    @staticmethod
    def get(character_id: int) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM sect_member WHERE character_id = ? AND is_defected = 0",
            (character_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def upsert(member: dict) -> int:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO sect_member
              (character_id, sect_id, path_id, joined_at_days, contribution,
               low_quests_done, mid_quests_done, high_quests_done,
               unlocked_mid, unlocked_high, is_defected, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            ON CONFLICT(character_id) DO UPDATE SET
              contribution = excluded.contribution,
              low_quests_done = excluded.low_quests_done,
              mid_quests_done = excluded.mid_quests_done,
              high_quests_done = excluded.high_quests_done,
              unlocked_mid = excluded.unlocked_mid,
              unlocked_high = excluded.unlocked_high,
              version = sect_member.version + 1
            """,
            (
                member["character_id"], member["sect_id"], member["path_id"],
                member["joined_at_days"], member["contribution"],
                member["low_quests_done"], member["mid_quests_done"],
                member["high_quests_done"],
                int(member["unlocked_mid"]), int(member["unlocked_high"]),
            ),
        )
        return member["character_id"]


class SectReputationRepository:

    @staticmethod
    def get(character_id: int, sect_id: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM sect_reputation WHERE character_id = ? AND sect_id = ?",
            (character_id, sect_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def upsert(rep: dict) -> None:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, low_quests_done, mid_quests_done,
               unlocked_mid, contribution, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(character_id, sect_id) DO UPDATE SET
              reputation = excluded.reputation,
              low_quests_done = excluded.low_quests_done,
              mid_quests_done = excluded.mid_quests_done,
              unlocked_mid = excluded.unlocked_mid,
              contribution = excluded.contribution,
              version = sect_reputation.version + 1
            """,
            (
                rep["character_id"], rep["sect_id"], rep["reputation"],
                rep["low_quests_done"], rep["mid_quests_done"],
                int(rep["unlocked_mid"]), rep["contribution"],
            ),
        )


class ActiveQuestRepository:

    @staticmethod
    def insert(q: dict) -> int:
        conn = get_conn()
        cur = conn.execute(
            """
            INSERT INTO sect_active_quest
              (character_id, sect_id, level_id, quest_kind, target_meta,
               reward_contribution, reward_reputation, reward_spirit_stone,
               accepted_at_days, expire_at_days, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted')
            """,
            (
                q["character_id"], q["sect_id"], q["level"], q["quest_kind"],
                json.dumps(q["target_meta"], ensure_ascii=False),
                q["reward_contribution"], q["reward_reputation"],
                q["reward_spirit_stone"], q["accepted_at_days"],
                q["expire_at_days"],
            ),
        )
        return cur.lastrowid

    @staticmethod
    def get(quest_id: int) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM sect_active_quest WHERE quest_id = ?",
            (quest_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["target_meta"] = json.loads(d["target_meta"])
        return d

    @staticmethod
    def update_status(quest_id: int, status: str) -> None:
        conn = get_conn()
        conn.execute(
            "UPDATE sect_active_quest SET status = ? WHERE quest_id = ?",
            (status, quest_id),
        )

    @staticmethod
    def count_today(character_id: int, level: str, today_day: int) -> int:
        conn = get_conn()
        cur = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM sect_active_quest
            WHERE character_id = ? AND level_id = ?
              AND accepted_at_days = ? AND status = 'accepted'
            """,
            (character_id, level, today_day),
        )
        return cur.fetchone()["cnt"]


class QuestRecordRepository:

    @staticmethod
    def insert(record: dict) -> int:
        conn = get_conn()
        cur = conn.execute(
            """
            INSERT INTO sect_quest_record
              (character_id, sect_id, level_id, quest_kind, role_path,
               contribution_gained, reputation_gained, spirit_stone_gained,
               completed_at_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["character_id"], record["sect_id"], record["level"],
                record["quest_kind"], record["role_path"],
                record["contribution_gained"], record["reputation_gained"],
                record["spirit_stone_gained"], record["completed_at_days"],
            ),
        )
        return cur.lastrowid


class ForbiddenLandProgressRepository:

    @staticmethod
    def get(character_id: int, land_id: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM forbidden_land_progress WHERE character_id = ? AND land_id = ?",
            (character_id, land_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def upsert(p: dict) -> None:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO forbidden_land_progress
              (character_id, land_id, is_first_cleared, entry_count_this_year,
               last_entry_at_days, version)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(character_id, land_id) DO UPDATE SET
              is_first_cleared = excluded.is_first_cleared,
              entry_count_this_year = excluded.entry_count_this_year,
              last_entry_at_days = excluded.last_entry_at_days,
              version = forbidden_land_progress.version + 1
            """,
            (
                p["character_id"], p["land_id"], int(p["is_first_cleared"]),
                p["entry_count_this_year"], p["last_entry_at_days"],
            ),
        )


class ForbiddenLandLogRepository:

    @staticmethod
    def insert(log: dict) -> int:
        conn = get_conn()
        cur = conn.execute(
            """
            INSERT INTO forbidden_land_log
              (character_id, land_id, role_path, sect_id, attempt,
               is_first_clear, rewards_json, success, entered_at_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["character_id"], log["land_id"], log["role_path"],
                log.get("sect_id"), log["attempt"],
                int(log["is_first_clear"]), log["rewards_json"],
                int(log["success"]), log["entered_at_days"],
            ),
        )
        return cur.lastrowid

    @staticmethod
    def count_attempts(character_id: int, land_id: str) -> int:
        conn = get_conn()
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM forbidden_land_log WHERE character_id = ? AND land_id = ?",
            (character_id, land_id),
        )
        return cur.fetchone()["cnt"]


class ExchangeLogRepository:

    @staticmethod
    def insert(log: dict) -> int:
        conn = get_conn()
        cur = conn.execute(
            """
            INSERT INTO sect_exchange_log
              (character_id, sect_id, role_path, item_type, item_id,
               cost_contribution, cost_reputation, exchanged_at_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["character_id"], log["sect_id"], log["role_path"],
                log["item_type"], log["item_id"],
                log["cost_contribution"], log["cost_reputation"],
                log["exchanged_at_days"],
            ),
        )
        return cur.lastrowid


# ---------------- 任务池初始化（种子数据） ----------------


SEED_QUESTS = [
    # (sect_id, level, kind, name, target_meta, reward_contribution_base, reward_spirit_stone_base, weight)
    ("qingyun_sword", "low", "patrol", "山门巡逻",
     {"target_npc": "剑宗外门执事", "duration_days": 12}, 30, 50, 10),
    ("qingyun_sword", "low", "delivery", "送剑至外门",
     {"target_npc": "镇上铁匠", "item_id": "iron_sword", "duration_days": 12}, 25, 80, 8),
    ("qingyun_sword", "mid", "suppression", "清剿三阶荒兽",
     {"target_monster": "iron_back_bear", "count": 1, "duration_days": 30}, 100, 200, 8),
    ("qingyun_sword", "high", "defense", "守住剑台",
     {"target_event": "sect_attack", "duration_days": 30}, 300, 500, 5),
    ("wanfa_spell", "low", "patrol", "法源洞天巡逻",
     {"target_npc": "法源守者", "duration_days": 12}, 30, 50, 10),
    ("wanfa_spell", "low", "delivery", "护送符箓",
     {"target_npc": "山下镇守", "item_id": "spirit_talisman", "duration_days": 12}, 25, 80, 8),
    ("wanfa_spell", "mid", "collection", "采集灵液",
     {"target_item": "spirit_liquid", "count": 5, "duration_days": 30}, 100, 200, 8),
    ("wanfa_spell", "high", "research", "整理法藏阁",
     {"target_event": "lore_arrange", "duration_days": 60}, 300, 500, 5),
    ("taishang_pill", "low", "patrol", "神农谷巡逻",
     {"target_npc": "丹房执事", "duration_days": 12}, 30, 50, 10),
    ("taishang_pill", "low", "delivery", "送药至山下",
     {"target_npc": "镇上医馆", "item_id": "blood_grass", "duration_days": 12}, 25, 80, 8),
    ("taishang_pill", "mid", "collection", "采集龙血草",
     {"target_item": "dragon_blood_grass", "count": 5, "duration_days": 30}, 100, 200, 8),
    ("taishang_pill", "high", "training", "炼丹试炉",
     {"target_event": "pill_craft", "duration_days": 60}, 300, 500, 5),
    ("xuanzhen_array", "low", "patrol", "阵衍山巡逻",
     {"target_npc": "阵法执事", "duration_days": 12}, 30, 50, 10),
    ("xuanzhen_array", "low", "delivery", "送阵图",
     {"target_npc": "下游坊市", "item_id": "array_blueprint", "duration_days": 12}, 25, 80, 8),
    ("xuanzhen_array", "mid", "suppression", "清剿阵衍山劫匪",
     {"target_monster": "array_bandit", "count": 1, "duration_days": 30}, 100, 200, 8),
    ("xuanzhen_array", "high", "defense", "守住阵眼",
     {"target_event": "sect_attack", "duration_days": 30}, 300, 500, 5),
    ("dahuang_body", "low", "patrol", "荒石岭巡逻",
     {"target_npc": "体修执事", "duration_days": 12}, 30, 50, 10),
    ("dahuang_body", "low", "training", "淬体对练",
     {"target_npc": "拳套教习", "duration_days": 12}, 25, 80, 8),
    ("dahuang_body", "mid", "suppression", "清剿三阶荒兽",
     {"target_monster": "iron_back_bear", "count": 1, "duration_days": 30}, 100, 200, 8),
    ("dahuang_body", "high", "defense", "守住山门",
     {"target_event": "sect_attack", "duration_days": 30}, 300, 500, 5),
    ("youming_demon", "low", "patrol", "九幽外域巡逻",
     {"target_npc": "魔道执事", "duration_days": 12}, 30, 50, 10),
    ("youming_demon", "low", "delivery", "送魂幡",
     {"target_npc": "魂幡楼", "item_id": "soul_flag", "duration_days": 12}, 25, 80, 8),
    ("youming_demon", "mid", "collection", "采集魂晶",
     {"target_item": "soul_crystal", "count": 5, "duration_days": 30}, 100, 200, 8),
    ("youming_demon", "high", "defense", "守住魔渊",
     {"target_event": "sect_attack", "duration_days": 30}, 300, 500, 5),
]


def seed_quest_pool() -> None:
    """把种子任务灌入 sect_quest_pool 表。"""
    conn = get_conn()
    # 清空再灌，便于幂等测试
    conn.execute("DELETE FROM sect_quest_pool")
    for row in SEED_QUESTS:
        sect_id, level, kind, name, target_meta, contrib, spirit, weight = row
        conn.execute(
            """
            INSERT INTO sect_quest_pool
              (sect_id, level_id, quest_kind, quest_name, target_meta,
               reward_contribution_base, reward_spirit_stone_base, weight)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sect_id, level, kind, name, json.dumps(target_meta, ensure_ascii=False),
                contrib, spirit, weight,
            ),
        )


class QuestPoolRepository:

    @staticmethod
    def draw(sect_id: str, level: str, game_year: Optional[int] = None) -> Optional[dict]:
        """按 weight 抽 1 个任务。

        v1.7：若传入 game_year 且该年已有 sect_quest_pool_yearly 记录，则用年度权重；
        否则回退到 sect_quest_pool 表的默认 weight。
        """
        import random
        conn = get_conn()

        if game_year is not None:
            # 优先用年度权重
            cur = conn.execute(
                """
                SELECT p.*, y.yearly_weight
                FROM sect_quest_pool p
                LEFT JOIN sect_quest_pool_yearly y
                  ON p.pool_id = y.pool_id
                 AND y.game_year = ?
                 AND y.sect_id = ?
                 AND y.level_id = ?
                WHERE p.sect_id = ? AND p.level_id = ?
                """,
                (game_year, sect_id, level, sect_id, level),
            )
            rows = cur.fetchall()
            # 如果年度权重表有数据，使用 yearly_weight；否则用 p.weight
            has_yearly = any(r["yearly_weight"] is not None for r in rows)
            if has_yearly:
                weights = [r["yearly_weight"] for r in rows]
            else:
                weights = [r["weight"] for r in rows]
        else:
            cur = conn.execute(
                "SELECT * FROM sect_quest_pool WHERE sect_id = ? AND level_id = ?",
                (sect_id, level),
            )
            rows = cur.fetchall()
            weights = [r["weight"] for r in rows]

        if not rows:
            return None
        total = sum(weights)
        if total <= 0:
            return None
        pick = random.uniform(0, total)
        upto = 0
        for r, w in zip(rows, weights):
            if upto + w >= pick:
                d = _row_to_dict(r)
                d["target_meta"] = json.loads(d["target_meta"])
                return d
            upto += w
        d = _row_to_dict(rows[-1])
        d["target_meta"] = json.loads(d["target_meta"])
        return d

    @staticmethod
    def refresh_yearly_weights(game_year: int) -> int:
        """刷新指定游戏年的任务池权重。

        算法：每个任务的年度权重 = base_weight × random(0.5, 1.5)
        保证权重不退化为 0（防止该任务本年度彻底不出），同时有年度差异感。
        返回灌入的记录数。
        """
        import random
        conn = get_conn()
        # 清掉该年旧记录（幂等）
        conn.execute("DELETE FROM sect_quest_pool_yearly WHERE game_year = ?", (game_year,))
        cur = conn.execute("SELECT pool_id, sect_id, level_id, weight FROM sect_quest_pool")
        rows = cur.fetchall()
        inserted = 0
        for r in rows:
            base = r["weight"]
            yearly = max(1, int(base * random.uniform(0.5, 1.5)))
            conn.execute(
                """
                INSERT INTO sect_quest_pool_yearly
                  (game_year, sect_id, level_id, pool_id, yearly_weight)
                VALUES (?, ?, ?, ?, ?)
                """,
                (game_year, r["sect_id"], r["level_id"], r["pool_id"], yearly),
            )
            inserted += 1
        return inserted


# ---------------- v1.7 新增：物品主表 Repository ----------------


class TechniqueRepository:

    @staticmethod
    def get(tech_id: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM technique WHERE tech_id = ? AND enabled = 1",
            (tech_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


class PillRepository:

    @staticmethod
    def get(pill_id: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM pill WHERE pill_id = ? AND enabled = 1",
            (pill_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


class EquipmentRepository:

    @staticmethod
    def get(equip_id: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM equipment WHERE equip_id = ? AND enabled = 1",
            (equip_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


# ---------------- v1.7 新增：散修通用贡献点钱包 ----------------


class SansxiuWalletRepository:

    @staticmethod
    def get(character_id: int) -> dict:
        """返回钱包，不存在则返回 0 钱包。"""
        conn = get_conn()
        cur = conn.execute(
            "SELECT * FROM sansxiu_wallet WHERE character_id = ?",
            (character_id,),
        )
        row = cur.fetchone()
        if row:
            return _row_to_dict(row)
        return {
            "character_id": character_id,
            "general_contribution": 0,
            "total_earned": 0,
            "version": 1,
        }

    @staticmethod
    def upsert(wallet: dict) -> None:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO sansxiu_wallet
              (character_id, general_contribution, total_earned, version)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(character_id) DO UPDATE SET
              general_contribution = excluded.general_contribution,
              total_earned = excluded.total_earned,
              version = sansxiu_wallet.version + 1
            """,
            (
                wallet["character_id"],
                wallet["general_contribution"],
                wallet["total_earned"],
            ),
        )

    @staticmethod
    def add_contribution(character_id: int, amount: int) -> dict:
        """原子加贡献点，返回更新后的钱包。"""
        wallet = SansxiuWalletRepository.get(character_id)
        wallet["general_contribution"] += amount
        if amount > 0:
            wallet["total_earned"] += amount
        SansxiuWalletRepository.upsert(wallet)
        return wallet


# ---------------- v1.7 新增：technique / pill / equipment 种子数据 ----------------
# 数据来自 v1.5 §4.4 / §4.9.5 共 41 部功法 + 配套丹药/装备
# 字段：(tech_id, name, tier, sub_tier, type, sect_id, base_price)
# tier 映射：凡=low / 灵=mid / 宝=mid（高）/ 圣=high / 帝=high
# 注：宝阶功法对散修兑换门槛仍按 mid 处理（§4.9.5 设定），故 tier 归 mid


SEED_TECHNIQUES = [
    # ---- 通用功法（§4.4.1，12 部） ----
    ("duanxue_jue", "《锻血诀》", "low", "凡·中品", "心法", None, 100),
    ("shanyue_quan", "《山岳拳》", "low", "凡·上品", "攻击", None, 200),
    ("tuna_qingmu", "《吐纳青木》", "low", "凡·下品", "辅助", None, 80),
    ("kurong_jing_can", "《枯荣经·残卷》", "mid", "灵·上品", "心法", None, 1800),
    ("pifu_yinlei", "《辟府引雷术》", "mid", "灵·中品", "攻击", None, 1200),
    ("guanshu_lang", "《观书郎》", "mid", "灵·下品", "辅助", None, 800),
    ("jiuyou_shihun_zhua", "《九幽噬魂爪》", "mid", "宝·中品", "攻击", None, 2500),
    ("jingang_buhuai_shen", "《金刚不坏身》", "high", "圣·下品", "防御", None, 6000),
    ("dijing_huangtian", "《帝经·荒天篇》", "high", "帝·上品", "心法", None, 10000),
    ("guankong_zhaixing", "《观空摘星》", "mid", "宝·上品", "辅助", None, 2500),
    ("qinglian_jian_ge_v1", "《青莲剑歌·卷一》", "mid", "宝·中品", "攻击", None, 2200),
    ("kurong_jing_zhong", "《枯荣经·终卷》", "high", "圣·上品", "心法", None, 7000),

    # ---- 青云剑宗独有（§4.9.5，4 部） ----
    ("qingfeng_three_strikes", "《青锋三式》", "mid", "灵·中品", "攻击", "qingyun_sword", 800),
    ("qingyun_jian_jue_v2_yixian", "《青云剑诀·一弦》", "mid", "灵·上品", "攻击", "qingyun_sword", 1800),
    ("wan_jian_guizong_chuwen", "《万剑归宗·初闻》", "mid", "宝·下品", "攻击", "qingyun_sword", 3200),
    ("qingyun_dian_dinglan", "《青云剑典·定澜》", "high", "圣·下品", "攻击", "qingyun_sword", 6000),

    # ---- 万法仙宗独有 ----
    ("guanwei_rumiao", "《观微入妙》", "mid", "灵·下品", "辅助", "wanfa_spell", 800),
    ("xiaocheng_wanfa_tu", "《小成万法图》", "mid", "灵·上品", "心法", "wanfa_spell", 1800),
    ("fadao_zhenjie_lingpian", "《法道真解·灵篇》", "mid", "宝·下品", "心法", "wanfa_spell", 3500),
    ("zhutian_facang", "《诸天法藏》", "high", "圣·中品", "心法", "wanfa_spell", 7000),

    # ---- 太上丹鼎独有 ----
    ("bianyao_baiwen", "《辨药百问》", "mid", "灵·下品", "辅助", "taishang_pill", 600),
    ("danfang_jichu", "《丹房基础》", "mid", "灵·中品", "辅助", "taishang_pill", 1200),
    ("baicao_tongxuan", "《百草通玄》", "mid", "宝·中品", "辅助", "taishang_pill", 2500),
    ("shennong_neijing_canj", "《神农内经·残卷》", "high", "圣·下品", "辅助", "taishang_pill", 6500),

    # ---- 玄阵堂独有 ----
    ("jianzhen_pu", "《简阵谱》", "mid", "灵·中品", "辅助", "xuanzhen_array", 900),
    ("guanzhen_yan", "《观阵眼》", "mid", "灵·上品", "辅助", "xuanzhen_array", 1800),
    ("sanqianjie_rumen", "《三千界·入门》", "mid", "宝·下品", "辅助", "xuanzhen_array", 2500),
    ("zhoutian_xingdou_zhenjing", "《周天星斗真经》", "high", "圣·下品", "辅助", "xuanzhen_array", 6500),

    # ---- 大荒体修门独有 ----
    ("cuiti_jue", "《淬体诀》", "mid", "灵·中品", "心法", "dahuang_body", 1000),
    ("dahuang_lianji_jue", "《大荒炼肌诀》", "mid", "灵·上品", "防御", "dahuang_body", 2000),
    ("dahuang_zhan_tian_lu_v2", "《大荒战天录》", "high", "圣·上品", "防御", "dahuang_body", 4000),
    ("bahuang_liuhe_bumie", "《八荒六合不灭体》", "high", "圣·中品", "防御", "dahuang_body", 5500),

    # ---- 幽冥魔道独有 ----
    ("shihun_mo_jing", "《噬魂魔经》", "mid", "宝·上品", "攻击", "youming_demon", 2000),
    ("xuesha_mo_dian", "《血煞魔典》", "mid", "宝·中品", "心法", "youming_demon", 1500),
    ("youming_zhen_jue", "《幽冥真诀》", "high", "圣·下品", "心法", "youming_demon", 4500),
    ("jiuyou_busi_zhang", "《九幽不死章》", "high", "帝·下品", "心法", "youming_demon", 8000),
]

SEED_PILLS = [
    # (pill_id, name, tier, sub_tier, effect, base_price)
    ("juqi_dan", "聚气丹", "low", "凡·下品", "立即获得 500 修为", 20),
    ("xi_sui_dan", "洗髓丹", "low", "凡·上品", "永久根骨 +1", 2000),
    ("po_jing_dan", "破境丹", "mid", "灵·中品", "突破 +8% 成功率", 5000),
    ("jingxin_dan", "静心丹", "mid", "灵·上品", "清除心境裂隙", 3000),
    ("peiyuan_dan", "培元丹", "mid", "灵·中品", "永久体魄 +3", 1500),
    ("xuming_dan", "续命丹", "mid", "宝·下品", "寿元 +50 年", 8000),
    ("jiuzhuan_huanshun", "九转还魂丹", "mid", "宝·上品", "死亡复活", 12000),
    ("xifan_dan", "洗凡丹", "mid", "宝·中品", "全属性 +2", 6000),
    ("fadao_dan", "法道丹", "mid", "宝·上品", "永久灵力 +5", 4000),
    ("cui_gu_gao", "体修淬骨膏", "mid", "宝·下品", "永久根骨 +2", 600),
    ("diyuan_dan", "帝源丹", "high", "帝·中品", "Lv.100 后帝王修为", 30000),
    ("shen_nong_danfang_yinyu", "神农丹方·隐语", "mid", "灵·中品", "解锁圣阶丹方", 1000),
]

SEED_EQUIPMENT = [
    # (equip_id, name, tier, sub_tier, slot, sect_id, base_price)
    ("shanyue_dajian", "山岳大剑", "low", "凡·精良", "weapon", None, 200),
    ("qingfeng_jian", "青锋剑", "low", "凡·稀有", "weapon", "qingyun_sword", 600),
    ("jiuyou_zhua", "九幽爪", "mid", "宝·史诗", "weapon", "youming_demon", 2500),
    ("xuanbu_daopao", "玄布道袍", "low", "凡·普通", "armor", None, 100),
    ("xuantie_zhanjia", "玄铁战甲", "mid", "灵·精良", "armor", None, 800),
    ("lingshi_guan", "灵识冠", "mid", "灵·普通", "helm", None, 800),
    ("fayuan_lingzhu", "法源灵珠", "mid", "灵·上品", "accessory", "wanfa_spell", 700),
    ("huangshi_huwan", "荒石护腕", "mid", "宝·上品", "accessory", "dahuang_body", 1500),
    ("shennong_liandan_lu", "神农炼丹炉", "mid", "宝·中品", "natal", "taishang_pill", 2000),
    ("yinhun_fan", "阴魂幡", "mid", "宝·中品", "natal", "youming_demon", 1500),
    ("yijie_chuwu_jie", "一阶储物戒", "low", "凡·普通", "storage", None, 500),
]


def seed_techniques() -> None:
    """灌入 technique / pill / equipment 种子。幂等。"""
    conn = get_conn()
    conn.execute("DELETE FROM technique")
    conn.execute("DELETE FROM pill")
    conn.execute("DELETE FROM equipment")
    for row in SEED_TECHNIQUES:
        tech_id, name, tier, sub_tier, tech_type, sect_id, base_price = row
        conn.execute(
            """
            INSERT INTO technique
              (tech_id, name, tier, sub_tier, type, sect_id, base_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tech_id, name, tier, sub_tier, tech_type, sect_id, base_price),
        )
    for row in SEED_PILLS:
        pill_id, name, tier, sub_tier, effect, base_price = row
        conn.execute(
            """
            INSERT INTO pill
              (pill_id, name, tier, sub_tier, effect, base_price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pill_id, name, tier, sub_tier, effect, base_price),
        )
    for row in SEED_EQUIPMENT:
        equip_id, name, tier, sub_tier, slot, sect_id, base_price = row
        conn.execute(
            """
            INSERT INTO equipment
              (equip_id, name, tier, sub_tier, slot, sect_id, base_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (equip_id, name, tier, sub_tier, slot, sect_id, base_price),
        )
