"""审查修复的回归用例（按批次追加）。

覆盖：
- P1-2：exp_overflow 必须在下一次结算回流；
- P1-1：@atomic 嵌套事务必须整体回滚（内层 Repository 不得提前提交）；
- P0-1：兑换校验必须前置，失败不得扣贡献；
- P0-2：发奖失败必须整体回滚，重复调用必须幂等。
"""
from __future__ import annotations

import pytest

from app.core.errors import ACTION_INVALID, ATTR_REQUIREMENT_NOT_MET, BAG_FULL
from app.db.game_repository import (
    CharacterRepository,
    GrantLogRepository,
    InventoryRepository,
    TechniqueRepository,
)
from app.db.repository import atomic, transaction
from app.db.repository import SectMemberRepository
from app.domain.level import apply_exp
from app.services import sect_bridge_service
from app.services.grant_service import GrantService


def _make_char(**overrides):
    data = {"name": "回归测试", "rng_seed": "seed-regression", "version": 1}
    data.update(overrides)
    return CharacterRepository.create(data)


def _make_disciple(char_id: int, contribution: int = 10000,
                   sect_id: str = "qingyun_sword") -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO sect_member "
            "(character_id, sect_id, path_id, joined_at_days, contribution) "
            "VALUES (?, ?, 'sword', 1, ?)",
            (char_id, sect_id, contribution),
        )


# ---------------- P1-2 / P1-1 ----------------

def test_exp_overflow_is_consumed_on_next_settlement():
    """5000 点溢出修为必须能续升，而不是沉在 exp_overflow 里。"""
    result = apply_exp(1, 0, 5000, 0, 10000, cap_level=18)
    assert result.level == 6
    assert result.cultivation == 918
    assert result.exp_overflow == 0
    assert result.levels_gained == 5


def test_partial_overflow_becomes_cultivation():
    """不足一级的溢出必须回填到 cultivation，而不是继续留在溢出池。"""
    result = apply_exp(1, 0, 50, 0, 10000, cap_level=18)
    assert result.level == 1
    assert result.cultivation == 50
    assert result.exp_overflow == 0


def test_atomic_rolls_back_nested_repository_writes():
    """@atomic 内调用 Repository（内层 transaction）失败时必须整体回滚。"""
    char_id = CharacterRepository.create({
        "name": "回归测试", "rng_seed": "seed-regression", "level": 1, "version": 1,
    })

    @atomic
    def _boom(cid: int, version: int) -> None:
        CharacterRepository.update(cid, {"level": 5}, expected_version=version)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _boom(char_id, 1)

    char = CharacterRepository.require(char_id)
    assert char["level"] == 1
    assert char["version"] == 1


# ---------------- P0-1 ----------------

def test_exchange_validates_realm_before_deducting():
    """境界不足必须在扣贡献之前拒绝，贡献不得变化。"""
    char_id = _make_char(level=1, realm_index=1)
    _make_disciple(char_id, contribution=10000)

    with pytest.raises(Exception) as exc:
        sect_bridge_service.exchange("technique", "qingfeng_three_strikes")

    assert getattr(exc.value, "code", None) == ATTR_REQUIREMENT_NOT_MET
    assert int(SectMemberRepository.get(char_id)["contribution"]) == 10000
    assert TechniqueRepository.get(char_id, "qingfeng_three_strikes") is None


def test_exchange_missing_content_does_not_deduct():
    """内容不存在必须在扣贡献之前拒绝。"""
    char_id = _make_char(level=10, realm_index=2)
    _make_disciple(char_id, contribution=10000)

    with pytest.raises(Exception) as exc:
        sect_bridge_service.exchange("equipment", "no_such_equipment_xyz")

    assert getattr(exc.value, "code", None) == ACTION_INVALID
    assert int(SectMemberRepository.get(char_id)["contribution"]) == 10000


# ---------------- P0-2 ----------------

def test_grant_bag_full_rolls_back_everything():
    """背包满时发奖必须整体回滚：修为/灵石/幂等日志都不得落库。"""
    char_id = _make_char(bag_slots=1)
    InventoryRepository.add(char_id, "juqi_dan", "pill", 1)

    with pytest.raises(Exception) as exc:
        GrantService.grant(
            char_id,
            {"exp": 100, "stones": 50,
             "items": [{"item_id": "xi_sui_dan", "qty": 1}]},
            source_type="test", source_ref="test:bagfull",
        )

    assert getattr(exc.value, "code", None) == BAG_FULL
    char = CharacterRepository.require(char_id)
    assert char["level"] == 1
    assert char["cultivation"] == 0
    assert char["spirit_stone"] == 0
    assert GrantLogRepository.exists(char_id, "test", "test:bagfull") is False


def test_grant_is_idempotent():
    """同一 source_ref 重复发奖只生效一次。"""
    char_id = _make_char()
    rewards = {"stones": 20}

    first = GrantService.grant(char_id, rewards,
                               source_type="test", source_ref="test:idem")
    second = GrantService.grant(char_id, rewards,
                                source_type="test", source_ref="test:idem")

    assert first["granted"] is True
    assert second["granted"] is False
    assert second["duplicate"] is True
    assert int(CharacterRepository.require(char_id)["spirit_stone"]) == 20


# ---------------- P1-3 / P1-4：突破根基 ----------------

def _maxed_realm1(**overrides):
    from app.content import get_registry
    from app.domain.level import exp_need

    registry = get_registry()
    need = exp_need(9, registry.exp_k_bp(9))
    data = {
        "name": "突破测试", "rng_seed": "seed-breakthrough", "version": 1,
        "level": 9, "realm_index": 1, "realm_stage": 9,
        "cultivation": need, "age_days": 1, "lifespan_max_days": 36000,
    }
    data.update(overrides)
    return CharacterRepository.create(data)


def test_foundation_next_bp_mapping():
    from app.domain import realm as realm_domain

    assert realm_domain.foundation_next_bp("圆满") == 300
    assert realm_domain.foundation_next_bp("崩坏") == -500
    assert realm_domain.foundation_next_bp("") == 0


def test_breakthrough_resets_realm_attempts_and_keeps_foundation(monkeypatch):
    """成功突破后 realm_attempts 归零；本境尝试次数决定根基。"""
    from app.services import breakthrough_service

    char_id = _maxed_realm1(realm_attempts=3, foundation="崩坏")
    monkeypatch.setattr(
        "app.services.breakthrough_service.RngService.roll_bp",
        lambda self, *args, **kwargs: True,
    )

    result = breakthrough_service.execute()

    assert result["success"] is True
    assert result["level"] == 10
    assert result["realm_index"] == 2
    char = CharacterRepository.require(char_id)
    assert char["realm_attempts"] == 0
    assert char["foundation"] == "崩坏"


def test_breakthrough_preview_uses_stored_foundation(monkeypatch):
    """存下的根基必须体现在下一境的成功率明细里（崩坏 = -500bp）。"""
    from app.services import breakthrough_service

    _maxed_realm1(realm_attempts=0, foundation="崩坏")
    preview = breakthrough_service.preview()
    items = {item["key"]: item["value_bp"] for item in preview["items"]}
    assert items["foundation"] == -500
