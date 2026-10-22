"""domain.event + domain.quest：抽签去重、判定公式、效果 DSL、目标推进。

文件名合并 event 与 quest 两模块（同一包内两份纯函数规则，用例数达标即可）。
"""
from __future__ import annotations

import pytest

from app.content import load_all
from app.core.rng import RngService
from app.domain import event, quest


# ---------------- 事件：抽签 ----------------

def test_candidates_filter_by_region_level_and_forced():
    """候选池按区域 + 等级过滤，主线强制事件默认不进随机池。"""
    events = [
        {"id": "a", "category": "resource", "weight": 30, "region_ids": ["back_hill"],
         "level_min": 1, "level_max": 9, "forced_by_quest": None},
        {"id": "b", "category": "npc", "weight": 25, "region_ids": ["herb_slope"],
         "level_min": 1, "level_max": 9, "forced_by_quest": None},
        {"id": "c", "category": "karma", "weight": 5, "region_ids": ["back_hill"],
         "level_min": 1, "level_max": 9, "forced_by_quest": "quest_06"},
        {"id": "d", "category": "trap", "weight": 8, "region_ids": ["back_hill"],
         "level_min": 10, "level_max": 18, "forced_by_quest": None},
    ]
    picked = event.candidates(events, region_id="back_hill", level=3, now_days=100)
    assert [e["id"] for e in picked] == ["a"]
    with_forced = event.candidates(events, region_id="back_hill", level=3,
                                   now_days=100, include_forced=True)
    assert [e["id"] for e in with_forced] == ["a", "c"]


def test_candidates_dedupe_within_thirty_months():
    """同一事件 30 游戏月（1800 日）内不重复；第 1801 日解除（R71）。"""
    events = [{"id": "a", "category": "npc", "weight": 25, "region_ids": ["back_hill"],
               "level_min": 1, "level_max": 9}]
    seen = {"a": 1000}
    assert event.candidates(events, region_id="back_hill", level=3,
                            now_days=1000 + 1799, last_seen=seen) == []
    assert len(event.candidates(events, region_id="back_hill", level=3,
                                now_days=1000 + 1800, last_seen=seen)) == 1


def test_trigger_target_bp_is_relative_for_sansxiu():
    """散修触发率按乘法：3500 × 1.10 = 3850（不是 4500）。"""
    assert event.trigger_target_bp("") == 3500
    assert event.trigger_target_bp("qingyun_sword") == 3500
    assert event.trigger_target_bp("sansxiu") == 3850


def test_weight_pool_and_pick_event():
    """加权池 = 类别权重 × 事件权重；空池返回 None（保底不空转）。"""
    events = [
        {"id": "a", "category": "resource", "weight": 30},
        {"id": "b", "category": "karma", "weight": 5},
        {"id": "c", "category": "unknown", "weight": 100},
    ]
    pool = event.weight_pool(events, {"resource": 3000, "karma": 500})
    assert [(e["id"], w) for e, w in pool] == [("a", 90000), ("b", 2500)]
    assert event.pick_event([], RngService("p" * 32, 1)) is None
    chosen = event.pick_event(pool, RngService("p" * 32, 1))
    assert chosen["id"] in {"a", "b"}


# ---------------- 事件：判定 ----------------

def test_success_bp_matches_r73_sample():
    """base 50% + 性格 +8% + 气运 5×0.3% = 59.5%（R73）。"""
    choice = {"check": {"attr": "wis", "difficulty_bp": 5000}}
    attrs = {"wis": 0, "luk": 5}
    assert event.success_bp(choice, attrs=attrs, personality_mod_bp=800) == 5950


def test_success_bp_attr_and_karma_modifiers():
    """属性按点加成；因果正负修正按 /100 与 /150 两套除数。"""
    choice = {"check": {"attr": "str", "difficulty_bp": 4000}}
    assert event.success_bp(choice, attrs={"str": 20, "luk": 0}) == 4000 + 20 * 5
    assert event.success_bp(choice, attrs={"str": 0, "luk": 0}, karma=100) == 4100
    assert event.success_bp(choice, attrs={"str": 0, "luk": 0}, karma=-150) == 3900


def test_success_bp_clamped_and_no_check_means_always():
    """成功率 clamp 5%–95%；无 check 的选项必定成功。"""
    assert event.success_bp({"check": {"attr": "luk", "difficulty_bp": 100}},
                            attrs={"luk": 0}) == 500
    assert event.success_bp({"check": {"attr": "luk", "difficulty_bp": 10000}},
                            attrs={"luk": 100}) == 9500
    assert event.success_bp({"check": None}, attrs={}) == 10000


def test_apply_effects_covers_all_dsl_types():
    """效果 DSL：属性/资源/因果/气血/状态/物品/功法/flag/buff 全部落地。"""
    effects = [
        {"type": "cultivation", "value": 300},
        {"type": "spirit_stone", "value": 250},
        {"type": "karma", "value": -30},
        {"type": "dao_heart", "value": -5},
        {"type": "hp_pct", "value": -40},
        {"type": "mp_pct", "value": 10},
        {"type": "attr", "key": "str", "value": 2},
        {"type": "item", "item_id": "zhixue_cao", "qty": 3},
        {"type": "item_loss", "item_id": "shou_gu", "qty": 1},
        {"type": "technique", "tech_id": "caomu_xin_jing"},
        {"type": "flag", "key": "village_cry_done"},
        {"type": "status", "key": "injured"},
        {"type": "favor", "npc_id": "lao_xiazi", "value": 10},
        {"type": "buff", "key": "cultivation_multiplier_bp", "value": 15000,
         "duration_days": 7},
        {"type": "unknown_effect", "value": 1},
    ]
    out = event.apply_effects(effects)
    assert out.cultivation == 300
    assert out.spirit_stone == 250
    assert out.karma == -30
    assert out.dao_heart == -5
    assert out.hp_pct == -40
    assert out.mp_pct == 10
    assert out.attr == {"str": 2}
    assert out.items_gained == {"zhixue_cao": 3}
    assert out.items_lost == {"shou_gu": 1}
    assert out.techniques == ["caomu_xin_jing"]
    assert out.flags == ["village_cry_done"]
    assert out.statuses == ["injured"]
    assert out.favors == {"lao_xiazi": 10}
    assert out.buffs[0]["duration_days"] == 7
    assert out.unhandled == [{"type": "unknown_effect", "value": 1}]


def test_effect_outcome_merge():
    """增量可叠加合并。"""
    a = event.apply_effects([{"type": "cultivation", "value": 100},
                             {"type": "attr", "key": "wis", "value": 1}])
    b = event.apply_effects([{"type": "cultivation", "value": 50},
                             {"type": "attr", "key": "wis", "value": 2}])
    a.merge(b)
    assert a.cultivation == 150
    assert a.attr["wis"] == 3


def test_resolve_choice_picks_branch_and_time_cost(monkeypatch):
    """一站式结算：命中取 success_effects，未命中取 fail_effects；耗时受 720 日上限约束。"""
    choice = {
        "id": "save",
        "check": {"attr": "str", "difficulty_bp": 5000},
        "time_cost_days": 30,
        "success_effects": [{"type": "karma", "value": 50}],
        "fail_effects": [{"type": "hp_pct", "value": -40}],
    }
    rng = RngService("r" * 32, 1)
    monkeypatch.setattr(event, "success_bp", lambda *a, **k: 10000)
    ok = event.resolve_choice(choice, attrs={"str": 10, "luk": 5}, rng=rng)
    monkeypatch.setattr(event, "success_bp", lambda *a, **k: 0)
    bad = event.resolve_choice(choice, attrs={"str": 10, "luk": 5}, rng=rng)

    assert ok["success"] is True and ok["effects"]["karma"] == 50
    assert bad["success"] is False and bad["effects"]["hp_pct"] == -40
    assert ok["time_cost_days"] == 30

    clamped = event.resolve_choice({**choice, "time_cost_days": 9999},
                                   attrs={"str": 10}, rng=rng)
    assert clamped["time_cost_days"] == 720


def test_resolve_is_deterministic_and_respects_bounds():
    """掷骰：0bp 必败、10000bp 必成；无 check 的选项直接成功。"""
    choice = {"check": {"attr": "wis", "difficulty_bp": 5000}}
    rng = RngService("d" * 32, 3)
    assert event.resolve(choice, 0, rng) is False
    assert event.resolve(choice, 10000, rng) is True
    assert event.resolve({"check": None}, 0, rng) is True
    assert event.resolve_choice(choice, attrs={"wis": 1}, rng=rng)["chance_bp"] > 0


def test_content_events_have_negative_cost_on_every_choice():
    """内容层 20 条事件：每个选项都有负面代价（R69）。"""
    content = load_all()
    assert len(content.events) == 20
    for ev in content.events:
        assert 2 <= len(ev["choices"]) <= 4
        for choice in ev["choices"]:
            effects = list(choice.get("success_effects") or ()) + \
                      list(choice.get("fail_effects") or ())
            negative = int(choice.get("time_cost_days", 0)) > 0
            for eff in effects:
                if eff["type"] in ("karma", "hp_pct", "mp_pct", "dao_heart") and eff.get("value", 0) < 0:
                    negative = True
                if eff["type"] == "attr" and eff.get("value", 0) < 0:
                    negative = True
                if eff["type"] == "item_loss":
                    negative = True
            assert negative, f"{ev['id']}/{choice['id']} 缺少负面代价"


def test_content_village_cry_is_forced_by_quest_06():
    """村里的哭声绑定主线 #6，且不进随机池。"""
    content = load_all()
    ev = content.event_by_id["village_cry"]
    assert ev["forced_by_quest"] == "quest_06"
    ids = [e["id"] for e in event.candidates(content.events, region_id="duanhuai_village",
                                             level=5, now_days=0)]
    assert "village_cry" not in ids


# ---------------- 主线：目标推进 ----------------

def _quest_demo() -> dict:
    return {
        "id": "quest_demo", "objective_rule": "all", "prereq": [],
        "objectives": [
            {"type": "kill", "enemy_id": "huangyuan_lang", "count": 3, "group": "main"},
            {"type": "collect", "item_id": "long_xue_cao", "count": 1, "group": "main"},
        ],
    }


def test_advance_increments_matching_objective():
    """击杀事件只推进匹配的 enemy_id，且封顶不溢出。"""
    q = _quest_demo()
    progress = quest.initial_progress(q)
    for _ in range(3):
        progress, changed = quest.advance(q, progress,
                                          {"type": "kill", "enemy_id": "huangyuan_lang"})
        assert changed is True
    assert progress["0"] == 3
    capped, changed4 = quest.advance(q, progress,
                                     {"type": "kill", "enemy_id": "huangyuan_lang"})
    assert changed4 is False and capped["0"] == 3
    untouched, changed = quest.advance(q, progress,
                                       {"type": "kill", "enemy_id": "dushan_shanmao"})
    assert changed is False and untouched["0"] == 3


def test_sync_absolute_objectives_idempotent():
    """绝对类目标（采集/等级）由当前状态推导，幂等且可越级补齐。"""
    q = _quest_demo()
    progress = quest.initial_progress(q)
    progress, changed = quest.sync(q, progress, {"items": {"long_xue_cao": 9}})
    assert changed is True and progress["1"] == 1
    again, changed2 = quest.sync(q, progress, {"items": {"long_xue_cao": 9}})
    assert changed2 is False and again["1"] == 1

    lv_quest = {"id": "q", "objective_rule": "all", "prereq": [],
                "objectives": [{"type": "reach_level", "level": 5, "count": 1, "group": "main"}]}
    p = quest.initial_progress(lv_quest)
    p, _ = quest.sync(lv_quest, p, {"level": 4})
    assert quest.is_ready(lv_quest, p) is False
    p, _ = quest.sync(lv_quest, p, {"level": 6})
    assert quest.is_ready(lv_quest, p) is True


def test_all_rule_requires_every_objective():
    """objective_rule=all 必须全部达成才算 ready。"""
    q = _quest_demo()
    progress = {"0": 3, "1": 0}
    assert quest.is_ready(q, progress) is False
    assert quest.is_ready(q, {"0": 3, "1": 1}) is True


def test_any_group_rule_for_sect_or_sansxiu():
    """objective_rule=any_group：宗门线或散修线任一完成即可（quest_12/17 口径）。"""
    q = {
        "id": "quest_12", "objective_rule": "any_group", "prereq": ["quest_11"],
        "objectives": [
            {"type": "complete_sect_quest", "count": 1, "group": "sect"},
            {"type": "sansxiu_encounter", "count": 1, "group": "sansxiu"},
        ],
    }
    assert quest.is_ready(q, {"0": 0, "1": 0}) is False
    assert quest.is_ready(q, {"0": 1, "1": 0}) is True
    assert quest.is_ready(q, {"0": 0, "1": 1}) is True


def test_join_sect_and_direction_matching():
    """join_sect 只认白名单宗门；choose_direction 只认白名单方向。"""
    q = {
        "id": "quest_11", "objective_rule": "any_group", "prereq": [],
        "objectives": [
            {"type": "join_sect", "accept_sect_ids": ["qingyun_sword"],
             "group": "sect"},
            {"type": "choose_direction", "directions": ["sword"], "group": "sect"},
            {"type": "join_sect", "accept_sect_ids": ["sansxiu"], "group": "sansxiu"},
        ],
    }
    p = quest.initial_progress(q)
    p, changed = quest.advance(q, p, {"type": "join_sect", "sect_id": "wanfa_spell"})
    assert changed is False
    p, changed = quest.advance(q, p, {"type": "join_sect", "sect_id": "sansxiu"})
    assert changed is True and p["2"] == 1
    assert quest.is_ready(q, p) is True


def test_status_and_prereq_chain():
    """状态机：locked → active → ready → completed。"""
    q = {"id": "quest_02", "objective_rule": "all", "prereq": ["quest_01"],
         "objectives": [{"type": "cultivate", "count": 1, "group": "main"}]}
    assert quest.status_of(q, completed=set(), progress={"0": 0}) == quest.STATUS_LOCKED
    assert quest.status_of(q, completed={"quest_01"}, progress={"0": 0}) == quest.STATUS_ACTIVE
    assert quest.status_of(q, completed={"quest_01"}, progress={"0": 1}) == quest.STATUS_READY
    assert quest.status_of(q, completed={"quest_01", "quest_02"},
                           progress={"0": 1}) == quest.STATUS_COMPLETED
    assert quest.claimable(q, {"0": 1}, {"quest_01"}) is True


def test_content_quest_chain_is_closed_and_covers_levels():
    """19 条主线：prereq 闭合、解锁链可逆、等级并集覆盖 1–18（无空洞）。"""
    content = load_all()
    ids = {q["id"] for q in content.quests}
    assert len(content.quests) == 19
    assert content.quests[0]["id"] == "quest_01"
    covered = set()
    for q in content.quests:
        assert set(q["prereq"]) <= ids
        assert set(q["unlock_next"]) <= ids
        covered |= set(range(int(q["level_min"]), int(q["level_max"]) + 1))
    assert covered == set(range(1, 19))
    assert quest.prereq_met(content.quests[0], set()) is True
    assert quest.auto_active_quests(content.quests, set()) == ("quest_01",)
    assert quest.auto_active_quests(content.quests, {"quest_01"}) == ("quest_02",)
