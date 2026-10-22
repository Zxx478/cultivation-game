"""兑换 Service 测试，覆盖弟子/散修资格校验。"""
from __future__ import annotations

import pytest

from app.config import load
from app.db import transaction
from app.models import RolePath
from app.services import (
    ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION,
    ERR_EXCHANGE_INSUFFICIENT_REPUTATION,
    ERR_EXCHANGE_INVALID_ITEM,
    ExchangeService,
    QuestService,
    SectServiceError,
)


def make_character(character_id: int) -> None:
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO character (id) VALUES (?)", (character_id,))


def make_disciple(character_id: int, sect_id: str = "qingyun_sword") -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_member (character_id, sect_id, path_id, joined_at_days, contribution)
            VALUES (?, ?, 'sword', 1, 0)
            """,
            (character_id, sect_id),
        )


def test_disciple_exchange_only_checks_contribution():
    make_character(6001)
    make_disciple(6001, "qingyun_sword")

    # 弟子初始 contribution=0，兑换凡阶功法价 30 → 应失败
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            6001, "qingyun_sword", "technique",
            "qingfeng_three_strikes", cost_contribution=30, today_day=10,
        )
    assert exc.value.code == ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION

    # 给弟子加贡献点（直接 SQL 模拟）
    with transaction() as conn:
        conn.execute(
            "UPDATE sect_member SET contribution = 100 WHERE character_id = ?",
            (6001,),
        )

    # 现在兑换凡阶（low tier）应成功
    result = ExchangeService.check_and_exchange(
        6001, "qingyun_sword", "technique",
        "qingfeng_three_strikes", cost_contribution=30, today_day=10,
    )
    assert result["success"] is True
    assert result["remaining_contribution"] == 70


def test_disciple_cannot_exchange_other_sect_items():
    make_character(6002)
    make_disciple(6002, "qingyun_sword")
    with transaction() as conn:
        conn.execute(
            "UPDATE sect_member SET contribution = 1000 WHERE character_id = ?",
            (6002,),
        )

    # 弟子想兑换万法仙宗物品 → 失败
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            6002, "wanfa_spell", "technique",
            "guanwei_rumiao", cost_contribution=100, today_day=10,
        )
    assert exc.value.code == ERR_EXCHANGE_INVALID_ITEM


def test_sansxiu_exchange_requires_reputation_for_low_tier():
    make_character(7001)
    # 散修初始 reputation=0，contribution=0
    # 兑换凡阶需 reputation >= 100，应失败
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            7001, "qingyun_sword", "technique",
            "qingfeng_three_strikes", cost_contribution=30, today_day=10,
        )
    assert exc.value.code == ERR_EXCHANGE_INSUFFICIENT_REPUTATION


def test_sansxiu_exchange_blocks_sacred_tier_under_500_reputation():
    make_character(7002)
    # 给散修 +400 好感度 + 6000 贡献点
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, contribution, unlocked_mid)
            VALUES (?, 'qingyun_sword', 400, 6000, 1)
            """,
            (7002,),
        )
    # 圣阶功法（high tier）需要 500 好感度，400 < 500 → 失败
    # 用真实存在的圣阶《青云剑典·定澜》(qingyun_dian_dinglan, base_price=6000)
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            7002, "qingyun_sword", "technique",
            "qingyun_dian_dinglan",    # 圣阶 -> high tier，查表 base_price=6000
            cost_contribution=6000, today_day=10,
        )
    # 2003 或 2004 均可（圣阶被特殊处理）
    assert exc.value.code in (
        ERR_EXCHANGE_INSUFFICIENT_REPUTATION,
    )


def test_sansxiu_exchange_mid_tier_requires_250_reputation():
    make_character(7003)
    # 200 好感度，灵阶功法需 250 → 失败
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, contribution, unlocked_mid)
            VALUES (?, 'qingyun_sword', 200, 5000, 1)
            """,
            (7003,),
        )
    # 用真实存在的灵阶《青锋三式》(qingfeng_three_strikes, tier=mid, base_price=800)
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            7003, "qingyun_sword", "technique",
            "qingfeng_three_strikes",    # 灵·中品 -> mid tier
            cost_contribution=2000, today_day=10,
        )
    assert exc.value.code == ERR_EXCHANGE_INSUFFICIENT_REPUTATION


def test_sansxiu_exchange_low_tier_succeeds_with_100_reputation():
    make_character(7004)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, contribution, unlocked_mid)
            VALUES (?, 'qingyun_sword', 100, 200, 0)
            """,
            (7004,),
        )
    # 用真实存在的凡阶《锻血诀》(duanxue_jue, tier=low, base_price=100)
    result = ExchangeService.check_and_exchange(
        7004, "qingyun_sword", "technique",
        "duanxue_jue",    # 凡·中品 -> low tier
        cost_contribution=30, today_day=10,
    )
    assert result["success"] is True
    assert result["remaining_contribution"] == 170
    assert result["remaining_reputation"] == 100    # 不扣好感度


def test_sansxiu_exchange_item_not_found_rejected():
    """v1.7 新增：物品不在 technique/pill/equipment 表中时报 2005。"""
    make_character(7500)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, contribution, unlocked_mid)
            VALUES (?, 'qingyun_sword', 100, 1000, 1)
            """,
            (7500,),
        )
    from app.services import ERR_EXCHANGE_ITEM_NOT_FOUND
    with pytest.raises(SectServiceError) as exc:
        ExchangeService.check_and_exchange(
            7500, "qingyun_sword", "technique",
            "fake_nonexistent_tech",    # 表中查不到
            cost_contribution=100, today_day=10,
        )
    assert exc.value.code == ERR_EXCHANGE_ITEM_NOT_FOUND


def test_sansxiu_exchange_auto_uses_base_price_when_cost_zero():
    """v1.7：调用方未传 cost_contribution（=0）时，自动从物品表查 base_price。"""
    make_character(7600)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO sect_reputation
              (character_id, sect_id, reputation, contribution, unlocked_mid)
            VALUES (?, 'qingyun_sword', 100, 200, 0)
            """,
            (7600,),
        )
    # 不传 cost_contribution（默认 0），service 应自动用《锻血诀》base_price=100
    result = ExchangeService.check_and_exchange(
        7600, "qingyun_sword", "technique",
        "duanxue_jue",    # 凡·中品 -> low tier, base_price=100
        cost_contribution=0, today_day=10,
    )
    assert result["success"] is True
    assert result["cost_contribution"] == 100    # 自动用 base_price
    assert result["remaining_contribution"] == 100    # 200 - 100 = 100
