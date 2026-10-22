"""禁地 Service 测试。"""
from __future__ import annotations

import pytest

from app.config import load
from app.db import transaction
from app.services import (
    ERR_FORBIDDEN_LAND_CAP_REACHED,
    ERR_FORBIDDEN_LAND_IN_COOLDOWN,
    ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION,
    ERR_FORBIDDEN_LAND_INVALID_ID,
    ERR_FORBIDDEN_LAND_REALM_TOO_LOW,
    ForbiddenLandService,
    SectServiceError,
)


def make_character(character_id: int) -> None:
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO character (id) VALUES (?)", (character_id,))


def make_disciple(character_id: int, sect_id: str = "qingyun_sword", contribution: int = 0) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_member (character_id, sect_id, path_id, joined_at_days, contribution)
            VALUES (?, ?, 'sword', 1, ?)
            """,
            (character_id, sect_id, contribution),
        )


def test_invalid_land_id_rejected():
    make_character(8001)
    with pytest.raises(SectServiceError) as exc:
        ForbiddenLandService.enter(8001, "fake_land", today_day=1,
                                   current_realm_seq=1, current_game_year=1)
    assert exc.value.code == ERR_FORBIDDEN_LAND_INVALID_ID


def test_realm_too_low_rejected():
    make_character(8002)
    make_disciple(8002, contribution=1000)
    # 古井剑渊 required_realm_seq=1，散修 realm_seq=0 -> 失败
    with pytest.raises(SectServiceError) as exc:
        ForbiddenLandService.enter(8002, "gujing_jianyuan",
                                   today_day=1, current_realm_seq=0,
                                   current_game_year=1)
    assert exc.value.code == ERR_FORBIDDEN_LAND_REALM_TOO_LOW


def test_disciple_first_clear_succeeds():
    make_character(8101)
    make_disciple(8101, contribution=200)
    result = ForbiddenLandService.enter(
        8101, "gujing_jianyuan",
        today_day=1, current_realm_seq=2, current_game_year=1,
    )
    assert result["is_first_clear"] is True
    assert result["cost_contribution"] == 50
    assert result["remaining_contribution"] == 150
    # 首通必给功法（drop_rate=1.0）
    technique_rewards = [r for r in result["rewards"] if r["type"] == "technique"]
    assert len(technique_rewards) >= 1


def test_disciple_repeat_clear_no_guaranteed_technique():
    make_character(8102)
    make_disciple(8102, contribution=2000)
    # 首通
    ForbiddenLandService.enter(8102, "gujing_jianyuan",
                               today_day=1, current_realm_seq=2,
                               current_game_year=1)
    # 第二次：冷却需过 30 日
    with pytest.raises(SectServiceError) as exc:
        ForbiddenLandService.enter(8102, "gujing_jianyuan",
                                   today_day=20, current_realm_seq=2,
                                   current_game_year=1)
    assert exc.value.code == ERR_FORBIDDEN_LAND_IN_COOLDOWN

    # 第 31 天：可再进，且首通已用过，走 repeat_rewards
    result = ForbiddenLandService.enter(8102, "gujing_jianyuan",
                                        today_day=31, current_realm_seq=2,
                                        current_game_year=1)
    assert result["is_first_clear"] is False


def test_cap_per_game_year():
    """每游戏年最多 3 次进入。"""
    make_character(8103)
    make_disciple(8103, contribution=2000)
    cfg = load()
    cap = cfg.forbidden_entries_per_game_year    # 3

    days = [1, 31, 61]
    for d in days:
        ForbiddenLandService.enter(8103, "gujing_jianyuan",
                                   today_day=d, current_realm_seq=2,
                                   current_game_year=1)
    # 第 4 次（隔 30 日）：拒绝
    with pytest.raises(SectServiceError) as exc:
        ForbiddenLandService.enter(8103, "gujing_jianyuan",
                                   today_day=91, current_realm_seq=2,
                                   current_game_year=1)
    assert exc.value.code == ERR_FORBIDDEN_LAND_CAP_REACHED

    # 进入下一年（today=361 是第 2 年第 1 天）：次数清零，可再进
    result = ForbiddenLandService.enter(8103, "gujing_jianyuan",
                                       today_day=400, current_realm_seq=2,
                                       current_game_year=2)
    assert result["entry_count_this_year"] == 1


def test_insufficient_contribution_rejected():
    make_character(8104)
    make_disciple(8104, contribution=10)    # 古井剑渊 cost 50
    with pytest.raises(SectServiceError) as exc:
        ForbiddenLandService.enter(8104, "gujing_jianyuan",
                                   today_day=1, current_realm_seq=2,
                                   current_game_year=1)
    assert exc.value.code == ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION


def test_sansxiu_uses_aggregated_contribution():
    """v1.7：散修使用 sansxiu_wallet.general_contribution（独立字段）作为通用贡献点。

    旧 v1.6 实现：散修贡献点 = ∑ 6 宗门散修贡献点
    v1.7 新实现：散修完成任务时按 sansxiu_general_share 比例累积到 wallet
    """
    make_character(8201)
    # 直接注入散修通用钱包
    from app.db import SansxiuWalletRepository
    SansxiuWalletRepository.upsert({
        "character_id": 8201,
        "general_contribution": 70,
        "total_earned": 70,
        "version": 1,
    })
    # 古井剑渊 cost=50，70 > 50 → 可进，扣后剩 20
    result = ForbiddenLandService.enter(8201, "gujing_jianyuan",
                                        today_day=1, current_realm_seq=2,
                                        current_game_year=1)
    assert result["role_path"] == "sansxiu"
    assert result["cost_contribution"] == 50
    assert result["remaining_contribution"] == 20


def test_sansxiu_general_contribution_accumulates_from_quest():
    """v1.7：散修完成任务时按 sansxiu_general_share 比例累积到 wallet。"""
    from app.db import SansxiuWalletRepository
    from app.services import QuestService

    make_character(8301)
    # 散修完成 1 个 low 任务
    # 任务贡献点取 10-50 范围，sansxiu_general_share.low = 0.3
    # 所以 general_contribution 累积约 = reward × 0.3
    q = QuestService.take(8301, "qingyun_sword", "low",
                           today_day=1, game_year=1)
    QuestService.complete(8301, q["quest_id"], today_day=2)

    wallet = SansxiuWalletRepository.get(8301)
    # 验证有累积（具体值取决于随机 reward，但应 > 0）
    assert wallet["general_contribution"] > 0
    # 期望：general_contribution ≈ reward_contribution × 0.3
    # 由于 reward 在 10-50，累积应在 3-15 之间
    assert 3 <= wallet["general_contribution"] <= 15


def test_sansxiu_wallet_empty_for_disciple():
    """弟子身份下 sansxiu_wallet 不应被写入。"""
    from app.db import SansxiuWalletRepository
    from app.services import QuestService

    make_character(8401)
    make_disciple(8401, contribution=0)

    q = QuestService.take(8401, "qingyun_sword", "low",
                           today_day=1, game_year=1)
    QuestService.complete(8401, q["quest_id"], today_day=2)

    wallet = SansxiuWalletRepository.get(8401)
    # 弟子完成 low 任务不应累积 wallet
    assert wallet["general_contribution"] == 0


def test_quest_pool_yearly_weights_refresh_per_game_year():
    """v1.7：任务池权重每游戏年刷新，同年内稳定。"""
    from app.db import QuestPoolRepository
    from app.services import QuestService

    make_character(8501)
    make_disciple(8501, contribution=0)

    # 第 1 年接任务（首次触发惰性权重加载）
    q1 = QuestService.take(8501, "qingyun_sword", "low",
                            today_day=1, game_year=1)
    assert q1 is not None

    # 检查 sect_quest_pool_yearly 表中 game_year=1 有记录
    from app.db.repository import _thread_local
    conn = _thread_local.conn
    cur = conn.execute(
        "SELECT COUNT(*) AS cnt FROM sect_quest_pool_yearly WHERE game_year = 1",
    )
    assert cur.fetchone()["cnt"] > 0

    # 第 2 年应该重新刷新权重
    QuestPoolRepository.refresh_yearly_weights(2)
    cur = conn.execute(
        "SELECT COUNT(*) AS cnt FROM sect_quest_pool_yearly WHERE game_year = 2",
    )
    assert cur.fetchone()["cnt"] > 0
