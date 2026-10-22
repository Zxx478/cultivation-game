"""审查修复的回归用例（按批次追加）。

覆盖：
- P1-2：exp_overflow 必须在下一次结算回流；
- P1-1：@atomic 嵌套事务必须整体回滚（内层 Repository 不得提前提交）。
"""
from __future__ import annotations

import pytest

from app.db.game_repository import CharacterRepository
from app.db.repository import atomic
from app.domain.level import apply_exp


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
