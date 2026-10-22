"""domain.combat：回合状态机、确定性、逃跑、禁逃、回灵、连战、超时、复活、JSON 往返。"""
from __future__ import annotations

import json

import pytest

from app.core.errors import COMBAT_ACTION_INVALID, COMBAT_ENDED, FLEE_BANNED, GameError
from app.core.rng import RngService
from app.domain import combat

PLAYER = {
    "id": "player", "name": "你", "kind": "player",
    "hp": 300, "hp_max": 300, "mp": 60, "mp_max": 60,
    "atk": 30.0, "def": 12.0, "dr_denom": 800.0, "pen": 20.0,
    "crit_bp": 500, "crit_dmg_bp": 15000, "hit_bp": 8900, "evade_bp": 300,
    "init": 25.0,
    "skills": [{"id": "shanyue_quan", "name": "山岳拳", "power_pct": 130,
                "mp_cost": 8, "cooldown": 2, "effect": "破甲"}],
}

ENEMY = {
    "id": "dushan_shanmao", "name": "独眼山猫", "kind": "minion",
    "hp": 180, "hp_max": 180, "mp": 40, "mp_max": 40,
    "atk": 22.0, "def": 8.0, "dr_denom": 500.0, "pen": 11.0,
    "crit_bp": 800, "crit_dmg_bp": 15000, "hit_bp": 8800, "evade_bp": 200,
    "init": 12.0, "skills": [],
}


def _tank() -> dict:
    """高血量玩家（用于纯逃跑测试，避免被打死）。"""
    return {**PLAYER, "hp": 10 ** 6, "hp_max": 10 ** 6}


def test_step_is_deterministic_for_same_seed_and_counter():
    """同 (seed, counter, stream) 必得同一结果（红线 12）。"""
    state = combat.build_state(PLAYER, ENEMY)
    a = combat.step(state, {"type": "attack"}, RngService("s" * 32, 7))
    b = combat.step(state, {"type": "attack"}, RngService("s" * 32, 7))
    assert a["enemy"]["hp"] == b["enemy"]["hp"]
    assert a["player"]["hp"] == b["player"]["hp"]
    assert a["log"] == b["log"]
    # 换 counter 结果应不同
    c = combat.step(state, {"type": "attack"}, RngService("s" * 32, 8))
    assert c["log"] != a["log"]


def test_state_json_roundtrip():
    """状态可 JSON 往返（战斗会话持久化 + 刷新恢复）。"""
    state = combat.build_state(PLAYER, ENEMY)
    state = combat.step(state, {"type": "attack"}, RngService("k" * 32, 1))
    payload = combat.to_json(state)
    restored = combat.from_json(payload)
    assert json.loads(payload) == state
    assert restored == state
    assert restored["turn"] == state["turn"]


def test_attack_reduces_enemy_hp_and_advances_turn():
    """普攻造成伤害、回合 +1、战报留痕。"""
    state = combat.build_state(PLAYER, ENEMY)
    nxt = combat.step(state, {"type": "attack"}, RngService("a" * 32, 3))
    assert nxt["turn"] == 2
    assert nxt["enemy"]["hp"] < state["enemy"]["hp"]
    assert nxt["log"], "战报不能为空"
    assert state["enemy"]["hp"] == 180, "原状态不得被原地修改"


def test_invalid_action_and_finished_state():
    """未知指令抛 4026；已结束会话再推进抛 4014。"""
    state = combat.build_state(PLAYER, ENEMY)
    with pytest.raises(GameError) as exc:
        combat.step(state, {"type": "dance"}, RngService("a" * 32, 1))
    assert exc.value.code == COMBAT_ACTION_INVALID

    finished = dict(state)
    finished["status"] = combat.STATUS_VICTORY
    with pytest.raises(GameError) as exc2:
        combat.step(finished, {"type": "attack"}, RngService("a" * 32, 1))
    assert exc2.value.code == COMBAT_ENDED


def test_skill_requires_mp_and_cooldown():
    """技能：灵力不足 or 冷却中 → 4026。"""
    poor = {**PLAYER, "mp": 0}
    state = combat.build_state(poor, ENEMY)
    with pytest.raises(GameError):
        combat.step(state, {"type": "skill", "skill_id": "shanyue_quan"},
                    RngService("a" * 32, 1))

    state2 = combat.build_state(PLAYER, ENEMY)
    after = combat.step(state2, {"type": "skill", "skill_id": "shanyue_quan"},
                        RngService("a" * 32, 1))
    # 释放当回合记 2，回合末统一 −1 → 剩 1
    assert after["player"]["cooldowns"].get("shanyue_quan") == 1
    with pytest.raises(GameError):
        combat.step(after, {"type": "skill", "skill_id": "shanyue_quan"},
                    RngService("a" * 32, 2))


def test_pen_multiplier_three_tiers():
    """破防三档：PEN/敌防 ≥1.0 → 1.15；≥0.3 → 1.00；否则 0.80（R43）。"""
    assert combat.pen_multiplier(50, 50) == pytest.approx(1.15)
    assert combat.pen_multiplier(20, 50) == pytest.approx(1.00)
    assert combat.pen_multiplier(5, 50) == pytest.approx(0.80)
    assert combat.pen_multiplier(10, 0) == pytest.approx(1.15)


def test_defend_raises_effective_dr():
    """防御指令使减伤显著提高（def ×1.5 参与减伤，R44）。"""
    normal = combat.dr_bp_of(50.0, 800.0, False)
    guarding = combat.dr_bp_of(50.0, 800.0, True)
    assert guarding > normal
    assert normal == 588


def test_flee_chance_clamped_and_boss_penalised():
    """逃跑率 clamp 5%–90%，BOSS −30%、秘境 −20%（红线 7）。"""
    state = combat.build_state(PLAYER, ENEMY)
    plain = combat.flee_chance_bp(state)
    boss = combat.flee_chance_bp(combat.build_state(PLAYER, ENEMY, is_boss=True))
    secret = combat.flee_chance_bp(combat.build_state(PLAYER, ENEMY, is_secret=True))
    assert plain == 4760           # 4500 + (25-12)*20 = 4760
    assert boss == plain - 3000
    assert secret == plain - 2000
    assert combat.flee_chance_bp(state, {"flee_base_bp": 99999}) <= 9000
    assert combat.flee_chance_bp(state, {"flee_base_bp": 0}) >= 500


def _seed_with_three_flee_failures() -> str:
    """找一个让前三次逃跑全部失败的种子（确定性搜索）。"""
    for i in range(500):
        seed = f"flee-{i}"
        rng = RngService(seed, 0)
        state = combat.build_state(_tank(), ENEMY, is_boss=True, is_secret=True)
        for _ in range(3):
            state = combat.step(state, {"type": "flee"}, rng)
            if state["status"] != combat.STATUS_ACTIVE:
                break
        if state["player"]["flee_fail"] >= 3 and state["status"] == combat.STATUS_ACTIVE:
            return seed
    raise AssertionError("no seed produced three flee failures")


def test_flee_banned_after_three_failures():
    """连续逃跑失败 3 次后禁逃（4027）。"""
    seed = _seed_with_three_flee_failures()
    rng = RngService(seed, 0)
    state = combat.build_state(_tank(), ENEMY, is_boss=True, is_secret=True)
    for _ in range(3):
        state = combat.step(state, {"type": "flee"}, rng)
    assert state["flags"]["flee_banned"] is True
    with pytest.raises(GameError) as exc:
        combat.step(state, {"type": "flee"}, rng)
    assert exc.value.code == FLEE_BANNED


def test_mp_regen_three_percent_per_turn():
    """回合结束回灵力 3%（上限内）。"""
    player = {**PLAYER, "mp": 0, "mp_max": 100}
    state = combat.build_state(player, ENEMY)
    after = combat.step(state, {"type": "defend"}, RngService("m" * 32, 1))
    assert after["player"]["mp"] == 3
    assert after["enemy"]["mp"] == 40      # 已满，不会再涨


def test_timeout_after_max_turns():
    """30 回合上限（此处用 max_turns=2 验证转移）。"""
    state = combat.build_state(_tank(), {**ENEMY, "hp": 10 ** 6, "hp_max": 10 ** 6},
                               max_turns=2)
    rng = RngService("t" * 32, 1)
    state = combat.step(state, {"type": "defend"}, rng)
    assert state["status"] == combat.STATUS_ACTIVE
    state = combat.step(state, {"type": "defend"}, rng)
    assert state["status"] == combat.STATUS_TIMEOUT


def test_consecutive_battle_multiplier_and_fatigue():
    """连战：第 3 场敌方 ×1.30；第 5 场起玩家力竭 −20%（R47）。"""
    base = combat.build_state(PLAYER, ENEMY, consec_battle=1)
    third = combat.build_state(PLAYER, ENEMY, consec_battle=3)
    assert third["flags"]["consec_multiplier_bp"] == 13000
    assert third["enemy"]["atk"] == pytest.approx(base["enemy"]["atk"] * 1.3)
    capped = combat.build_state(PLAYER, ENEMY, consec_battle=99)
    assert capped["flags"]["consec_multiplier_bp"] == 16000

    fifth = combat.build_state(PLAYER, ENEMY, consec_battle=5)
    assert fifth["flags"]["fatigued"] is True
    assert fifth["player"]["atk"] == pytest.approx(PLAYER["atk"] * 0.8)


def test_victory_then_revive():
    """击杀 → victory；死亡 → defeat，还魂丹可拉回 active 并回血（红线：还魂丹）。"""
    weak = {**ENEMY, "hp": 1, "hp_max": 1, "atk": 0.0}
    state = combat.build_state({**PLAYER, "atk": 999.0}, weak)
    won = combat.step(state, {"type": "attack"}, RngService("v" * 32, 1))
    assert won["status"] == combat.STATUS_VICTORY
    assert won["enemy"]["hp"] == 0

    strong = {**ENEMY, "hp": 99999, "hp_max": 99999, "atk": 99999.0, "hit_bp": 9900}
    doomed = combat.build_state({**PLAYER, "hp": 1, "hp_max": 1}, strong)
    lost = combat.step(doomed, {"type": "defend"}, RngService("v" * 32, 2))
    assert lost["status"] == combat.STATUS_DEFEAT
    revived = combat.apply_revive(lost, hp_pct=50)
    assert revived["status"] == combat.STATUS_ACTIVE
    assert revived["player"]["hp"] == max(1, lost["player"]["hp_max"] // 2)
    assert revived["flags"]["revived"] is True


def test_enemy_from_content_maps_skills():
    """enemies.yaml 行 → 战斗属性（含麻痹类技能的 stun_bp 推导）。"""
    row = {
        "id": "mijing_shouwei", "name": "秘境守卫", "kind": "elite",
        "hp": 1900, "atk": 118, "def": 66, "speed": 30,
        "skills": [{"name": "府门一戟", "power_pct": 160, "mp_cost": 20,
                    "cooldown": 3, "effect": "破甲 20%"},
                   {"name": "镇守", "power_pct": 90, "mp_cost": 30,
                    "cooldown": 5, "effect": "30% 概率麻痹 1 回合"}],
    }
    enemy = combat.enemy_from_content(row)
    assert enemy["init"] == 30
    assert len(enemy["skills"]) == 2
    assert enemy["skills"][0]["stun_bp"] == 0
    assert enemy["skills"][1]["stun_bp"] == 3000
