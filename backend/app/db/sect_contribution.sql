-- ============================================================
-- 《荒天纪》宗门任务与贡献系统 DDL（v1.6）
-- ============================================================
-- 设计原则：
-- 1. 服务端 SQLite 唯一权威，所有写操作走乐观锁
-- 2. 散修对各宗门独立维护 好感度 + 贡献点 双轨
-- 3. 已完成任务计数分级累积，便于解锁判定
-- 4. 与已有 `character` / `technique` / `inventory` 表关联
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- 0. character 主表占位 ----------
-- 完整 character 表见 02-技术方案 §5 DDL；此处仅保证 FK 不报错。
-- 真实项目应统一在 main schema 里建表，本文件只附属于 sect 子系统。
CREATE TABLE IF NOT EXISTS character (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    age_days        INTEGER NOT NULL DEFAULT 0,
    path_id         TEXT NOT NULL DEFAULT '',
    sect_id         TEXT,
    realm_major     INTEGER NOT NULL DEFAULT 1,
    realm_minor     INTEGER NOT NULL DEFAULT 1,
    -- 字段留位，便于扩展
    -- 实际 character 表的字段更多，以 v1.3 §5 DDL 为准
    -- 此处只确保 FK 引用可达
    placeholder     INTEGER NOT NULL DEFAULT 1
);

-- ---------- 1. 宗门弟子关系表 ----------
-- 玩家入宗后，每个角色同时只能属于 1 个宗门
CREATE TABLE IF NOT EXISTS sect_member (
    character_id    INTEGER NOT NULL,
    sect_id         TEXT NOT NULL,
    path_id         TEXT NOT NULL,        -- body/sword/spell/alchemy/array/demon，魔道路径叠加
    joined_at_days  INTEGER NOT NULL,     -- 入宗时的 age_days
    contribution    INTEGER NOT NULL DEFAULT 0,  -- 当前宗门贡献点
    -- 弟子任务解锁计数（每宗门独立，但弟子只属一宗所以只记一宗）
    low_quests_done   INTEGER NOT NULL DEFAULT 0,
    mid_quests_done   INTEGER NOT NULL DEFAULT 0,
    high_quests_done  INTEGER NOT NULL DEFAULT 0,
    -- 当前是否已解锁 mid / high
    unlocked_mid       INTEGER NOT NULL DEFAULT 0,    -- 0 / 1
    unlocked_high      INTEGER NOT NULL DEFAULT 0,
    -- 退出标志：叛宗或退宗后置 1
    is_defected        INTEGER NOT NULL DEFAULT 0,
    version            INTEGER NOT NULL DEFAULT 1,    -- 乐观锁
    PRIMARY KEY (character_id),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sect_member_sect ON sect_member(sect_id);

-- ---------- 2. 散修好感度表（按宗门独立） ----------
-- 散修对所有 6 宗门各维护好感度；散修身份标签在 character.path_id='sansxiu' 上
CREATE TABLE IF NOT EXISTS sect_reputation (
    character_id  INTEGER NOT NULL,
    sect_id       TEXT NOT NULL,
    reputation    INTEGER NOT NULL DEFAULT 0,    -- 好感度（正负皆可，魔道路径会扣减）
    -- 散修对该宗门任务的完成计数（按等级独立）
    low_quests_done   INTEGER NOT NULL DEFAULT 0,
    mid_quests_done   INTEGER NOT NULL DEFAULT 0,
    -- 散修对该宗门是否解锁 mid
    unlocked_mid    INTEGER NOT NULL DEFAULT 0,
    -- 散修在该宗门累计的"贡献点"，仅用于兑换本宗物品
    contribution    INTEGER NOT NULL DEFAULT 0,
    version         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (character_id, sect_id),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sect_rep_sect ON sect_reputation(sect_id);

-- ---------- 3. 进行中的任务表 ----------
-- 一名角色同时只能持有有限个任务（daily_cap 限制）
CREATE TABLE IF NOT EXISTS sect_active_quest (
    quest_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id  INTEGER NOT NULL,
    sect_id       TEXT NOT NULL,         -- 任务来源宗门
    level_id      TEXT NOT NULL,         -- low / mid / high
    quest_kind    TEXT NOT NULL,         -- 如 patrol / escort / suppression
    target_meta   TEXT NOT NULL,         -- JSON: { target_npc / target_monster / item_id / count }
    reward_contribution INTEGER NOT NULL,
    reward_reputation  INTEGER NOT NULL DEFAULT 0,  -- 散修才用
    reward_spirit_stone INTEGER NOT NULL DEFAULT 0,
    accepted_at_days  INTEGER NOT NULL,
    expire_at_days   INTEGER NOT NULL,  -- 超期自动失败
    status        TEXT NOT NULL DEFAULT 'accepted',  -- accepted / completed / failed / expired
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_active_quest_character ON sect_active_quest(character_id, status);

-- ---------- 4. 已完成任务记录表 ----------
-- 用于任务解锁阈值判定、人生大事记、统计
CREATE TABLE IF NOT EXISTS sect_quest_record (
    record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id  INTEGER NOT NULL,
    sect_id       TEXT NOT NULL,
    level_id      TEXT NOT NULL,
    quest_kind    TEXT NOT NULL,
    role_path     TEXT NOT NULL,    -- disciple / sansxiu  当前身份
    contribution_gained  INTEGER NOT NULL,
    reputation_gained    INTEGER NOT NULL DEFAULT 0,
    spirit_stone_gained  INTEGER NOT NULL DEFAULT 0,
    completed_at_days  INTEGER NOT NULL,
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_quest_record_char_sect ON sect_quest_record(character_id, sect_id, level_id);

-- ---------- 5. 禁地探索记录表（MVP 开放 2 处） ----------
CREATE TABLE IF NOT EXISTS forbidden_land_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id    INTEGER NOT NULL,
    land_id         TEXT NOT NULL,
    role_path       TEXT NOT NULL,    -- disciple / sansxiu
    sect_id         TEXT,             -- 弟子身份时的宗门；散修可为 NULL
    attempt         INTEGER NOT NULL,  -- 第几次尝试
    is_first_clear  INTEGER NOT NULL DEFAULT 0,
    rewards_json    TEXT NOT NULL,    -- JSON 数组：[{type, id, amount}]
    success         INTEGER NOT NULL DEFAULT 1,
    entered_at_days INTEGER NOT NULL,
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_forbidden_log_char ON forbidden_land_log(character_id, land_id);

-- ---------- 6. 禁地进度表（每个角色对每处禁地的状态） ----------
CREATE TABLE IF NOT EXISTS forbidden_land_progress (
    character_id    INTEGER NOT NULL,
    land_id         TEXT NOT NULL,
    is_first_cleared  INTEGER NOT NULL DEFAULT 0,  -- 是否已首通
    entry_count_this_year  INTEGER NOT NULL DEFAULT 0,  -- 本游戏年累计进入次数
    last_entry_at_days  INTEGER,    -- 上次进入时间，用于 cooldown
    version           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (character_id, land_id),
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 7. 宗门兑换日志 ----------
CREATE TABLE IF NOT EXISTS sect_exchange_log (
    exchange_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id   INTEGER NOT NULL,
    sect_id        TEXT NOT NULL,
    role_path      TEXT NOT NULL,
    item_type      TEXT NOT NULL,    -- technique / pill / equipment / technique_recipe
    item_id        TEXT NOT NULL,
    cost_contribution  INTEGER NOT NULL,
    cost_reputation   INTEGER NOT NULL DEFAULT 0,  -- 散修扣的好感度（不扣，仅作记录阈值校验值）
    exchanged_at_days  INTEGER NOT NULL,
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_exchange_log_char ON sect_exchange_log(character_id, sect_id);

-- ---------- 8. 任务池（每日刷新表） ----------
-- 6 宗门 × 3 等级 各 6 任务 = 108 条种子任务
CREATE TABLE IF NOT EXISTS sect_quest_pool (
    pool_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sect_id         TEXT NOT NULL,
    level_id        TEXT NOT NULL,
    quest_kind      TEXT NOT NULL,
    quest_name      TEXT NOT NULL,
    target_meta     TEXT NOT NULL,    -- JSON
    reward_contribution_base INTEGER NOT NULL,    -- 基准，最终 = 基准 × 随机
    reward_spirit_stone_base INTEGER NOT NULL DEFAULT 0,
    weight          INTEGER NOT NULL DEFAULT 10    -- 抽签权重
);
CREATE INDEX IF NOT EXISTS idx_pool_sect_level ON sect_quest_pool(sect_id, level_id);

-- ---------- 9. technique / pill / equipment 物品主表（v1.7 新增） ----------
-- 用于 exchange_service 兑换资格校验时查询物品的 tier 字段
-- 替代 _classify_item_tier 的字符串关键词方案
CREATE TABLE IF NOT EXISTS technique (
    tech_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    tier           TEXT NOT NULL,    -- low / mid / high（对应凡/灵/宝+圣+帝）
    sub_tier       TEXT NOT NULL,    -- 凡·下 / 凡·中 / 凡·上 / 灵·下... / 圣·上 / 帝·上
    type           TEXT NOT NULL,    -- 心法 / 攻击 / 防御 / 辅助 / 方向专属
    sect_id        TEXT,              -- 宗门独有功法的所属宗门；通用功法 NULL
    base_price     INTEGER NOT NULL DEFAULT 0,    -- 兑换价（贡献点）
    enabled        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_tech_tier ON technique(tier);
CREATE INDEX IF NOT EXISTS idx_tech_sect ON technique(sect_id);

CREATE TABLE IF NOT EXISTS pill (
    pill_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    tier           TEXT NOT NULL,
    sub_tier       TEXT NOT NULL,
    effect         TEXT NOT NULL,
    base_price     INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS equipment (
    equip_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    tier           TEXT NOT NULL,
    sub_tier       TEXT NOT NULL,
    slot           TEXT NOT NULL,    -- weapon / armor / helm / accessory / natal / storage
    sect_id        TEXT,
    base_price     INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_equip_sect ON equipment(sect_id);

-- ---------- 10. 散修通用贡献点钱包表（v1.7 新增） ----------
-- 散修入宗转换前，禁地等"非宗门绑定"消费走此表
CREATE TABLE IF NOT EXISTS sansxiu_wallet (
    character_id        INTEGER PRIMARY KEY,
    general_contribution INTEGER NOT NULL DEFAULT 0,
    -- 历史累计（仅统计，不消费）
    total_earned        INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (character_id) REFERENCES character(id) ON DELETE CASCADE
);

-- ---------- 11. 任务池年度权重表（v1.7 新增） ----------
-- 每游戏年由 game_clock 触发刷新；同年内权重稳定
-- (game_year, sect_id, level_id, pool_id) 复合主键
CREATE TABLE IF NOT EXISTS sect_quest_pool_yearly (
    game_year    INTEGER NOT NULL,
    sect_id      TEXT NOT NULL,
    level_id     TEXT NOT NULL,
    pool_id      INTEGER NOT NULL,
    yearly_weight INTEGER NOT NULL,    -- 本年度该任务的抽签权重（0 表示本年度不出）
    PRIMARY KEY (game_year, sect_id, level_id, pool_id),
    FOREIGN KEY (pool_id) REFERENCES sect_quest_pool(pool_id) ON DELETE CASCADE
);