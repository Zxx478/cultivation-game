"""任务接取/完成/解锁的完整测试。覆盖：

- 弟子：low → mid 解锁阈值；mid → high 解锁阈值
- 散修：初始只能接 low；reputation 达阈值解锁 mid；不可接 high（硬规则）
- 任务当日上限 daily_cap
- 任务过期处理
"""
from __future__ import annotations

import pytest

from app.config import load
from app.db import (
    ActiveQuestRepository,
    SectMemberRepository,
    SectReputationRepository,
    transaction,
)
from app.models import QuestLevel, QuestStatus, RolePath
from app.services import (
    ERR_QUEST_DAILY_CAP_REACHED,
    ERR_QUEST_INVALID_SECT,
    ERR_QUEST_QUEST_EXPIRED,
    ERR_QUEST_ROLE_NOT_READY,
    ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH,
    QuestService,
    SectServiceError,
)


# ---------------- 工具：模拟入宗 / 散修身份 ----------------


def make_disciple(character_id: int, sect_id: str = "qingyun_sword", joined_day: int = 1) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_member (character_id, sect_id, path_id, joined_at_days)
            VALUES (?, ?, 'sword', ?)
            """,
            (character_id, sect_id, joined_day),
        )


def make_sansxiu(character_id: int) -> None:
    """散修不需要专门建表，但需要 character 表有一行（FK 约束）。

    测试用 fixture 直接 INSERT character(id) 绕过外键。
    """
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO character (id) VALUES (?)", (character_id,))


# ---------------- 弟子身份：low → mid 解锁 ----------------


def test_disciple_starts_with_low_only():
    make_sansxiu(1001)
    make_disciple(1001, "qingyun_sword")

    # 直接接 mid 应被拒
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(1001, "qingyun_sword", "mid", today_day=10)
    assert exc.value.code == ERR_QUEST_ROLE_NOT_READY

    # 接 low 应成功
    result = QuestService.take(1001, "qingyun_sword", "low", today_day=10)
    assert result["level"] == "low"
    assert result["sect_id"] == "qingyun_sword"


def test_disciple_unlocks_mid_after_10_low():
    make_sansxiu(1002)
    make_disciple(1002, "qingyun_sword")

    cfg = load()
    threshold = cfg.disciple_unlock.mid_required_low_quests    # 10

    # 完成 threshold - 1 个 low：还差 1
    for i in range(threshold - 1):
        q = QuestService.take(1002, "qingyun_sword", "low", today_day=10 + i)
        QuestService.complete(1002, q["quest_id"], today_day=11 + i)

    # 现在 mid 仍不可接
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(1002, "qingyun_sword", "mid", today_day=50)
    assert exc.value.code == ERR_QUEST_ROLE_NOT_READY

    # 再完成 1 个 low：解锁
    q = QuestService.take(1002, "qingyun_sword", "low", today_day=50)
    complete_result = QuestService.complete(1002, q["quest_id"], today_day=51)
    assert complete_result["unlocked_mid"] is True

    # 现在 mid 可接
    q_mid = QuestService.take(1002, "qingyun_sword", "mid", today_day=60)
    assert q_mid["level"] == "mid"


def test_disciple_unlocks_high_after_15_mid():
    make_sansxiu(1003)
    make_disciple(1003, "qingyun_sword")

    cfg = load()
    # 先把 low 阈值跑通解锁 mid
    for i in range(cfg.disciple_unlock.mid_required_low_quests):
        q = QuestService.take(1003, "qingyun_sword", "low", today_day=10 + i)
        QuestService.complete(1003, q["quest_id"], today_day=11 + i)
    # 解锁 mid 后跑 mid
    for i in range(cfg.disciple_unlock.high_required_mid_quests - 1):
        q = QuestService.take(1003, "qingyun_sword", "mid", today_day=200 + i * 2)
        QuestService.complete(1003, q["quest_id"], today_day=201 + i * 2)

    # high 仍不可接
    with pytest.raises(SectServiceError):
        QuestService.take(1003, "qingyun_sword", "high", today_day=500)

    # 再完成 1 个 mid 解锁 high
    q = QuestService.take(1003, "qingyun_sword", "mid", today_day=500)
    complete_result = QuestService.complete(1003, q["quest_id"], today_day=501)
    assert complete_result["unlocked_high"] is True

    q_high = QuestService.take(1003, "qingyun_sword", "high", today_day=510)
    assert q_high["level"] == "high"


# ---------------- 散修身份：reputation → mid 解锁 ----------------


def test_sansxiu_starts_low_only():
    make_sansxiu(2001)

    # 散修初始可直接接 low（无 sect_member 记录）
    result = QuestService.take(2001, "qingyun_sword", "low", today_day=10)
    assert result["level"] == "low"
    # 散修接任务应附带 reputation 奖励
    assert result["reward_reputation"] >= 5


def test_sansxiu_cannot_take_high_ever():
    make_sansxiu(2002)
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(2002, "qingyun_sword", "high", today_day=10)
    assert exc.value.code == ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH


def test_sansxiu_unlocks_mid_when_reputation_reaches_threshold():
    make_sansxiu(2003)
    cfg = load()
    threshold = cfg.sansxiu_unlock.mid_required_reputation    # 200

    # 一直完成 low 任务，直到 reputation 达阈值
    # 每次 low 至少 +5，最多 +15，平均 ~10。我们多跑几次保证超阈值
    unlocked = False
    today = 1
    while not unlocked:
        q = QuestService.take(2003, "qingyun_sword", "low", today_day=today)
        result = QuestService.complete(2003, q["quest_id"], today_day=today + 1)
        unlocked = result["unlocked_mid"]
        today += 2
        # 安全阀：跑 100 次必达
        if today > 200:
            break

    assert unlocked is True, "散修多次完成 low 任务后必须解锁 mid"

    # 解锁后散修可接 mid
    q_mid = QuestService.take(2003, "qingyun_sword", "mid", today_day=today)
    assert q_mid["level"] == "mid"


def test_sansxiu_reputation_independent_per_sect():
    """散修对 6 宗门的 reputation 独立计算。"""
    make_sansxiu(2004)
    cfg = load()
    threshold = cfg.sansxiu_unlock.mid_required_reputation

    # 只对青云剑宗做 low 任务，到达阈值
    today = 1
    unlocked = False
    while not unlocked:
        q = QuestService.take(2004, "qingyun_sword", "low", today_day=today)
        result = QuestService.complete(2004, q["quest_id"], today_day=today + 1)
        unlocked = result["unlocked_mid"]
        today += 2

    # 对万法仙宗：reputation 仍为 0，mid 不可接
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(2004, "wanfa_spell", "mid", today_day=today + 10)
    assert exc.value.code == ERR_QUEST_ROLE_NOT_READY


# ---------------- daily_cap 限制 ----------------


def test_daily_cap_blocks_excess_low():
    make_sansxiu(3001)
    make_disciple(3001, "qingyun_sword")
    cfg = load()
    cap = cfg.quest_levels["low"].daily_cap    # 5

    # 接 cap 个：成功
    for i in range(cap):
        q = QuestService.take(3001, "qingyun_sword", "low", today_day=10)
        assert q is not None

    # 第 cap + 1 个：拒绝
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(3001, "qingyun_sword", "low", today_day=10)
    assert exc.value.code == ERR_QUEST_DAILY_CAP_REACHED

    # 第二天接：成功
    q = QuestService.take(3001, "qingyun_sword", "low", today_day=11)
    assert q is not None


# ---------------- 任务过期 ----------------


def test_quest_expired_when_today_exceeds_expire():
    make_sansxiu(4001)
    q = QuestService.take(4001, "qingyun_sword", "low", today_day=1)
    # low 任务有效期 7 天
    # 在第 10 天完成：超过 expire_at_days=8
    with pytest.raises(SectServiceError) as exc:
        QuestService.complete(4001, q["quest_id"], today_day=10)
    assert exc.value.code == ERR_QUEST_QUEST_EXPIRED

    # 任务状态已置 expired
    quest = ActiveQuestRepository.get(q["quest_id"])
    assert quest["status"] == QuestStatus.EXPIRED.value


# ---------------- 宗门 ID 校验 ----------------


def test_invalid_sect_id_rejected():
    make_sansxiu(5001)
    with pytest.raises(SectServiceError) as exc:
        QuestService.take(5001, "fake_sect", "low", today_day=1)
    assert exc.value.code == ERR_QUEST_INVALID_SECT
