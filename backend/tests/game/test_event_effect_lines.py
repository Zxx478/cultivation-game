"""事件展示契约：效果行必须中文化，内部键名不得外泄。

覆盖用户反馈的两个点：
- 事件结果里的属性加成一类文案不能出现 ``str`` / ``luk`` / ``chance_bp`` 等英文键名；
- ``deltas`` 旧字段保持兼容，前端旧逻辑不至于崩。
"""
from __future__ import annotations

import json

from app.content import get_registry
from app.db.game_repository import CharacterRepository
from app.services import effect_lines, story_event_service


def _make_char(**overrides):
    data = {"name": "契约测试", "rng_seed": "seed-effect-lines", "version": 1}
    data.update(overrides)
    return CharacterRepository.create(data)


def _pending(event_id: str, instance_id: str = "inst-effect-lines") -> str:
    return json.dumps({
        "instance_id": instance_id,
        "event_id": event_id,
        "created_at_days": 1,
    })


def _labels(lines):
    return [str(line.get("label", "")) for line in lines]


def _texts(lines):
    return [str(line.get("text", "")) for line in lines]


def test_effect_lines_translate_attr_and_items_to_chinese():
    """属性键、状态键、物品 id 都必须换成中文名。"""
    registry = get_registry()
    lines = effect_lines.build(
        {
            "attr": {"luk": 2, "bon": -1},
            "cultivation": 300,
            "karma": 50,
            "spirit_stone": 0,
            "hp_pct": -40,
            "items_gained": {"juqi_dan": 2},
            "items_lost": {"juqi_dan": 1},
            "statuses": ["injured"],
            "flags": ["some_internal_flag"],
            "unhandled": [],
        },
        registry,
        time_days=30,
    )

    labels = _labels(lines)
    texts = _texts(lines)

    assert "气运" in labels
    assert "根骨" in labels
    assert "修为" in labels
    assert "因果" in labels
    assert "气血" in labels
    assert "所得·聚气丹" in labels
    assert "失去·聚气丹" in labels
    assert "状态" in labels

    assert "+2" in texts
    assert "-40%" in texts
    assert "×2" in texts
    assert "负伤" in texts
    assert "30 日" in texts

    # 零值不占行
    assert "灵石" not in labels
    # 内部标记不外泄
    assert "some_internal_flag" not in texts
    assert all("internal" not in text for text in texts)


def test_effect_lines_never_leak_ascii_keys():
    """任何标签都不允许是纯英文键名（chance_bp / success 尤其不行）。"""
    lines = effect_lines.build(
        {
            "success": True,
            "chance_bp": 6500,
            "effects": {"attr": {"luk": 1}},
            "cultivation": 100,
            "unknown_engine_key": 3,
            "flags": ["x"],
        },
        get_registry(),
        time_days=0,
    )

    labels = _labels(lines)
    assert labels
    for label in labels:
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label), label
    for text in _texts(lines):
        assert "chance_bp" not in text
        assert "unknown_engine_key" not in text
    # 成功率不展示（设计 §3.5）
    assert all("chance" not in label for label in labels)


def test_event_choose_returns_effect_lines_and_keeps_deltas():
    """choose() 回包要带中文效果行，同时保留旧的 deltas 结构。"""
    char_id = _make_char(level=5, cultivation=0, exp_overflow=0)
    CharacterRepository.update(char_id, {"pending_event": _pending("village_cry")})

    result = story_event_service.choose("inst-effect-lines", "ignore")

    assert result["success"] is True
    lines = result.get("effect_lines")
    assert isinstance(lines, list) and lines, "必须返回可直接展示的效果行"
    labels = _labels(lines)
    assert "灵石" in labels and "因果" in labels
    for label in labels:
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label), label

    # 旧字段仍在，且事件已被清除
    assert "deltas" in result and "effects" in result["deltas"]
    assert result["deltas"]["spirit_stone"] == 200
    assert result["deltas"]["karma"] == -30
    char = CharacterRepository.require(char_id)
    assert not (char.get("pending_event") or "")


def test_effect_lines_covers_unknown_shape_without_crash():
    """空效果 / None / 纯数字都不能抛异常。"""
    registry = get_registry()
    assert effect_lines.build(None, registry) == []
    assert effect_lines.build({}, registry) == []

    lines = effect_lines.build({"cultivation": -50}, registry)
    assert _labels(lines) == ["修为"]
    assert _texts(lines) == ["-50"]


def test_mnd_attr_effect_actually_writes_dao_heart():
    """``attr: mnd`` 必须落到 dao_heart 列（角色表没有 mnd 列），不能静默丢写。"""
    char_id = _make_char(level=5, dao_heart=40)
    CharacterRepository.update(char_id, {"pending_event": _pending("yi_xiang_gan_wu", "inst-mnd")})

    result = story_event_service.choose("inst-mnd", "watch")

    char = CharacterRepository.require(char_id)
    assert int(char["dao_heart"]) == 43
    labels = _labels(result["effect_lines"])
    assert "道心" in labels
    assert all(label != "mnd" for label in labels)


def test_mnd_attr_effect_is_clamped_to_dao_heart_range():
    """道心 0–100 的夹取与 effects.dao_heart 分支保持一致。"""
    char_id = _make_char(level=5, dao_heart=99)
    CharacterRepository.update(char_id, {"pending_event": _pending("yi_xiang_gan_wu", "inst-mnd-cap")})

    story_event_service.choose("inst-mnd-cap", "watch")

    assert int(CharacterRepository.require(char_id)["dao_heart"]) == 100
