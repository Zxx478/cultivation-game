-- ============================================================
-- 《荒天纪》v1.5 · 游戏主表 DDL（game.sql）
-- ============================================================
-- 执行顺序铁律：本文件 **必须先于** sect_contribution.sql 执行。
--   game.sql 建完整 character → 宗门 DDL 里的占位
--   `CREATE TABLE IF NOT EXISTS character` 变成 no-op。
-- 兼容铁律：character 除 id 外**每一列都必须有 DEFAULT 或可 NULL**，
--   否则既有测试的 `INSERT OR IGNORE INTO character (id) VALUES (?)` 会失败。
-- 时间口径：所有时间字段存整数游戏日（age_days），不存时间戳语义的浮点。
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- 0. character 主表 ----------
CREATE TABLE IF NOT EXISTS character (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  name               TEXT    NOT NULL DEFAULT '',
  gender             TEXT    NOT NULL DEFAULT 'unknown',    -- male/female/unknown（仅文本描述）
  personality        TEXT    NOT NULL DEFAULT 'zhongyong',  -- 孤傲/温厚/狡黠/疯癫/中庸
  physique           TEXT    NOT NULL DEFAULT 'fan_ti',
  talent             INTEGER NOT NULL DEFAULT 5,            -- 资质 1–10 品
  level              INTEGER NOT NULL DEFAULT 1,            -- 全局等级 1–18（MVP）
  realm_index        INTEGER NOT NULL DEFAULT 1,            -- 大境序号 1–12
  realm_stage        INTEGER NOT NULL DEFAULT 1,            -- 小境 1–9
  cultivation        INTEGER NOT NULL DEFAULT 0,            -- 当前等级内累计修为
  exp_overflow       INTEGER NOT NULL DEFAULT 0,            -- 连升上限后的溢出（不丢失）
  hp                 INTEGER NOT NULL DEFAULT 0,            -- 0 = 按衍生公式取满
  mp                 INTEGER NOT NULL DEFAULT 0,
  age_days           INTEGER NOT NULL DEFAULT 1,            -- 第 1 日（D5 口径）
  lifespan_max_days  INTEGER NOT NULL DEFAULT 36000,        -- 100 年 × 360
  spirit_stone       INTEGER NOT NULL DEFAULT 0,
  karma              INTEGER NOT NULL DEFAULT 0,            -- −1000..1000
  dao_heart          INTEGER NOT NULL DEFAULT 10,           -- 0..100
  str                INTEGER NOT NULL DEFAULT 10,
  con                INTEGER NOT NULL DEFAULT 10,
  spr                INTEGER NOT NULL DEFAULT 10,
  sen                INTEGER NOT NULL DEFAULT 10,
  bon                INTEGER NOT NULL DEFAULT 10,
  wis                INTEGER NOT NULL DEFAULT 10,
  agi                INTEGER NOT NULL DEFAULT 10,
  luk                INTEGER NOT NULL DEFAULT 5,
  region_id          TEXT    NOT NULL DEFAULT 'duanhuai_village',
  path_id            TEXT    NOT NULL DEFAULT '',           -- ''=未择 / sansxiu / 6 宗门
  sect_id            TEXT,                                   -- 只读镜像，权威在 sect_member
  direction_id       TEXT    NOT NULL DEFAULT '',           -- 5 方向之一
  foundation         TEXT    NOT NULL DEFAULT '',           -- 圆满/扎实/虚浮/崩坏
  fail_streak        INTEGER NOT NULL DEFAULT 0,
  realm_attempts     INTEGER NOT NULL DEFAULT 0,            -- 本境突破尝试次数（根基主判据）
  bag_slots          INTEGER NOT NULL DEFAULT 60,
  pending_event      TEXT    NOT NULL DEFAULT '',           -- JSON 快照，'' = 无待决事件
  status             TEXT    NOT NULL DEFAULT 'normal',     -- normal/injured/qi_deviation/dead
  rng_seed           TEXT    NOT NULL DEFAULT '',           -- 32 字节 hex，绝不下发前端
  rng_counter        INTEGER NOT NULL DEFAULT 0,
  last_settle_at     INTEGER NOT NULL DEFAULT 0,             -- 仅用于"世事已变"，不结算离线收益
  version            INTEGER NOT NULL DEFAULT 1,             -- 乐观锁
  created_at         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_character_status ON character(status);
CREATE INDEX IF NOT EXISTS idx_character_path ON character(path_id);

-- ---------- 1. 角色功法 ----------
CREATE TABLE IF NOT EXISTS character_technique (
  char_id        INTEGER NOT NULL,
  tech_id        TEXT    NOT NULL,
  layer          INTEGER NOT NULL DEFAULT 1,     -- 1–9
  sense          INTEGER NOT NULL DEFAULT 0,     -- 感悟值
  equipped_slot  TEXT    NOT NULL DEFAULT '',    -- heart / atk1..3 / def1..3 / sup1..3
  learned_at     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, tech_id),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_char_tech_slot ON character_technique(char_id, equipped_slot);

-- ---------- 2. 角色装备（实例） ----------
CREATE TABLE IF NOT EXISTS character_equipment (
  instance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id       INTEGER NOT NULL,
  template_id   TEXT    NOT NULL,
  slot          TEXT    NOT NULL DEFAULT 'weapon',
  rarity        TEXT    NOT NULL DEFAULT 'common',
  enhance       INTEGER NOT NULL DEFAULT 0,      -- 0–5
  affixes_json  TEXT    NOT NULL DEFAULT '[]',
  equipped      INTEGER NOT NULL DEFAULT 0,      -- 0/1
  created_at    INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_char_equip_char ON character_equipment(char_id, equipped);

-- ---------- 3. 角色背包（材料/丹药/消耗品堆叠） ----------
CREATE TABLE IF NOT EXISTS character_inventory (
  char_id    INTEGER NOT NULL,
  item_id    TEXT    NOT NULL,
  item_type  TEXT    NOT NULL DEFAULT 'material',  -- pill/material/consumable
  qty        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, item_id),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 4. 角色主线进度 ----------
CREATE TABLE IF NOT EXISTS character_quest (
  char_id        INTEGER NOT NULL,
  quest_id       TEXT    NOT NULL,
  status         TEXT    NOT NULL DEFAULT 'locked',  -- locked/active/ready/completed
  step           INTEGER NOT NULL DEFAULT 0,
  progress_json  TEXT    NOT NULL DEFAULT '{}',
  updated_at     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, quest_id),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_char_quest_status ON character_quest(char_id, status);

-- ---------- 5. 角色标记（一次性事件/路径/去重） ----------
CREATE TABLE IF NOT EXISTS character_flag (
  char_id  INTEGER NOT NULL,
  key      TEXT    NOT NULL,
  value    TEXT    NOT NULL DEFAULT '',
  PRIMARY KEY (char_id, key),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 6. 角色与 NPC 关系 ----------
CREATE TABLE IF NOT EXISTS character_relation (
  char_id  INTEGER NOT NULL,
  npc_id   TEXT    NOT NULL,
  favor    INTEGER NOT NULL DEFAULT 0,   -- −100..100
  karma    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, npc_id),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 7. 战斗会话（一角色一会话） ----------
CREATE TABLE IF NOT EXISTS combat_session (
  char_id       INTEGER PRIMARY KEY,
  session_json  TEXT    NOT NULL DEFAULT '{}',
  status        TEXT    NOT NULL DEFAULT 'active',
  turn          INTEGER NOT NULL DEFAULT 1,
  updated_at    INTEGER NOT NULL DEFAULT 0,
  version       INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 8. 存档槽（0=自动，1–3 手动；**不设 FK**，快照独立于角色存活） ----------
CREATE TABLE IF NOT EXISTS save_slot (
  slot_id         INTEGER PRIMARY KEY,       -- 0 自动 / 1–3 手动
  char_id         INTEGER,
  label           TEXT    NOT NULL DEFAULT '',
  snapshot_json   TEXT    NOT NULL DEFAULT '{}',
  content_version TEXT    NOT NULL DEFAULT '',
  content_hash    TEXT    NOT NULL DEFAULT '',
  checksum        TEXT    NOT NULL DEFAULT '',
  created_at      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_save_slot_char ON save_slot(char_id);

-- ---------- 9. 事件日志（限长 500，插入后裁剪） ----------
CREATE TABLE IF NOT EXISTS event_log (
  log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id      INTEGER NOT NULL,
  kind         TEXT    NOT NULL DEFAULT 'info',
  payload_json TEXT    NOT NULL DEFAULT '{}',
  created_at   INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_log_char ON event_log(char_id, log_id);

-- ---------- 10. 里程碑日志（限长 200） ----------
CREATE TABLE IF NOT EXISTS milestone_log (
  log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id    INTEGER NOT NULL,
  title      TEXT    NOT NULL DEFAULT '',
  detail     TEXT    NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_milestone_char ON milestone_log(char_id, log_id);

-- ---------- 11. 元信息 ----------
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

-- ---------- 12. 发奖幂等日志 ----------
CREATE TABLE IF NOT EXISTS reward_grant_log (
  grant_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id      INTEGER NOT NULL,
  source_type  TEXT    NOT NULL,
  source_ref   TEXT    NOT NULL,
  payload_json TEXT    NOT NULL DEFAULT '{}',
  created_at   INTEGER NOT NULL DEFAULT 0,
  UNIQUE (char_id, source_type, source_ref),
  FOREIGN KEY (char_id) REFERENCES character(id) ON DELETE CASCADE
);
